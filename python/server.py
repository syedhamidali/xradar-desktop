"""WebSocket server entry point for xradar-desktop Python sidecar."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

_server_dir = Path(__file__).resolve().parent
if str(_server_dir) not in sys.path:
    sys.path.insert(0, str(_server_dir))

import websockets
from engine.exporter import RadarExporter
from engine.processor import RadarProcessor
from engine.reader import RadarReader, _serializable
from engine.renderer import RadarRenderer
from engine.tracking import CellInfo, identify_cells, track_cells, extrapolate_cell
from engine.retrievals import RETRIEVAL_REGISTRY, run_retrieval
from engine.statistics import get_statistics
from engine.dualpol import scatter_data, histogram_data, region_stats
from engine.qc import QC_REGISTRY, QCPipeline
from engine.processor import extract_cross_section, extract_vertical_profile
from engine.temporal import compute_time_series, compute_difference, compute_accumulation, compute_trend
from engine.volume import extract_volume, extract_cross_section_3d
from websockets.asyncio.server import ServerConnection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("xradar-server")

# WebSocket configuration — override via environment variables
WS_MAX_SIZE_MB = int(os.environ.get("XRADAR_WS_MAX_SIZE_MB", "10"))
WS_MAX_SIZE = WS_MAX_SIZE_MB * 1024 * 1024
EXPECTED_SESSION_TOKEN = os.environ.get("XRADAR_SESSION_TOKEN")
ALLOWED_ORIGIN_PREFIXES = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:",
    "http://127.0.0.1:",
)


# Shared application state — survives across WebSocket reconnections
_shared_reader = RadarReader()
_shared_renderer = RadarRenderer()
_shared_processor = RadarProcessor()
_shared_exporter = RadarExporter()

# Multi-file support: dict of file_id -> RadarReader instances
_file_readers: dict[str, RadarReader] = {}
_last_file_id: str | None = None  # most recently opened file
_render_lock = asyncio.Lock()
_mutation_lock = asyncio.Lock()


def _get_reader(file_id: str | None) -> RadarReader:
    """Resolve a file_id to its RadarReader, falling back to the shared reader."""
    global _last_file_id
    if file_id and file_id in _file_readers:
        return _file_readers[file_id]
    if _last_file_id and _last_file_id in _file_readers:
        return _file_readers[_last_file_id]
    return _shared_reader


def _get_datatree(file_id: str | None):
    return _get_reader(file_id).datatree


def _origin_allowed(ws: ServerConnection) -> bool:
    if not EXPECTED_SESSION_TOKEN:
        return True
    request = getattr(ws, "request", None)
    headers = getattr(request, "headers", None)
    origin = headers.get("Origin") if headers is not None else None
    if not origin:
        return True
    return any(origin.startswith(prefix) for prefix in ALLOWED_ORIGIN_PREFIXES)


class ConnectionHandler:
    """Manages message routing for a single WebSocket connection, using shared state."""

    def __init__(self, ws: ServerConnection) -> None:
        self.ws = ws

    async def send(self, msg: dict[str, Any]) -> None:
        try:
            await self.ws.send(json.dumps(msg, default=_json_default))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed while sending message")

    async def send_error(self, message: str) -> None:
        await self.send({"type": "error", "message": message})

    async def send_progress(self, percent: int, message: str) -> None:
        await self.send({"type": "progress", "percent": percent, "message": message})

    async def handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            await self.send_error(f"Invalid JSON: {exc}")
            return

        if not isinstance(msg, dict):
            await self.send_error("Message must be a JSON object")
            return

        msg_type = msg.get("type")
        if not msg_type:
            await self.send_error("Message missing 'type' field")
            return

        if EXPECTED_SESSION_TOKEN and msg.get("token") != EXPECTED_SESSION_TOKEN:
            await self.send_error("Unauthorized WebSocket message")
            return

        handler = {
            "open_file": self._handle_open_file,
            "close_file": self._handle_close_file,
            "render": self._handle_render,
            "get_sweep_data": self._handle_get_sweep_data,
            "process": self._handle_process,
            "export": self._handle_export,
            "batch_export": self._handle_batch_export,
            "export_animation": self._handle_export_animation,
            "identify_cells": self._handle_identify_cells,
            "track_cells": self._handle_track_cells,
            "run_retrieval": self._handle_run_retrieval,
            "get_cross_section": self._handle_get_cross_section,
            "get_vertical_profile": self._handle_get_vertical_profile,
            "get_statistics": self._handle_get_statistics,
            "list_retrievals": self._handle_list_retrievals,
            "get_time_series": self._handle_get_time_series,
            "get_difference": self._handle_get_difference,
            "get_accumulation": self._handle_get_accumulation,
            "run_qc": self._handle_run_qc,
            "list_qc_algorithms": self._handle_list_qc_algorithms,
            "get_data_table": self._handle_get_data_table,
            "get_metadata_tree": self._handle_get_metadata_tree,
            "ping": self._handle_ping,
            "get_scatter_data": self._handle_get_scatter_data,
            "get_histogram": self._handle_get_histogram,
            "get_region_stats": self._handle_get_region_stats,
            "get_volume": self._handle_get_volume,
            "get_cross_section_3d": self._handle_get_cross_section_3d,
        }.get(msg_type)

        if handler is None:
            await self.send_error(f"Unknown message type: {msg_type}")
            return

        try:
            await handler(msg)
        except (ValueError, TypeError) as exc:
            logger.warning("Validation error in '%s': %s", msg_type, exc)
            await self.send_error(f"Invalid input for {msg_type}: {exc}")
        except FileNotFoundError as exc:
            logger.warning("File not found in '%s': %s", msg_type, exc)
            await self.send_error(f"File not found: {exc}")
        except (KeyError, IndexError) as exc:
            logger.warning("Lookup error in '%s': %s", msg_type, exc)
            await self.send_error(f"Not found: {exc}")
        except OSError as exc:
            logger.error("I/O error in '%s': %s", msg_type, exc)
            await self.send_error(f"I/O error in {msg_type}: {exc}")
        except ImportError as exc:
            logger.error("Missing dependency in '%s': %s", msg_type, exc)
            await self.send_error(f"Missing dependency: {exc}")
        except Exception as exc:
            logger.error(
                "Unexpected error handling '%s': %s\n%s",
                msg_type,
                exc,
                traceback.format_exc(),
            )
            await self.send_error(f"Unexpected error in {msg_type}: {exc}")

    # ------------------------------------------------------------------
    # Dual-pol analysis handlers
    # ------------------------------------------------------------------

    async def _handle_get_scatter_data(self, msg: dict[str, Any]) -> None:
        """Return paired arrays for a scatter plot of two variables."""
        var1 = msg.get("var1")
        var2 = msg.get("var2")
        sweep = msg.get("sweep", 0)
        subsample = msg.get("subsample", 5000)
        color_var = msg.get("color_var")

        if not var1 or not isinstance(var1, str):
            await self.send_error("get_scatter_data: missing or invalid 'var1'")
            return
        if not var2 or not isinstance(var2, str):
            await self.send_error("get_scatter_data: missing or invalid 'var2'")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("get_scatter_data: 'sweep' must be a non-negative integer")
            return

        datatree = _get_datatree(msg.get("file_id"))
        if datatree is None:
            await self.send_error("get_scatter_data: no file is open")
            return

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: scatter_data(
                datatree, var1, var2, sweep,
                subsample=int(subsample),
                color_var=color_var,
            ),
        )

        await self.send({"type": "scatter_data_result", **result})

    async def _handle_get_histogram(self, msg: dict[str, Any]) -> None:
        """Compute histogram for a variable."""
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        bins = msg.get("bins", 50)

        if not variable or not isinstance(variable, str):
            await self.send_error("get_histogram: missing or invalid 'variable'")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("get_histogram: 'sweep' must be a non-negative integer")
            return

        datatree = _get_datatree(msg.get("file_id"))
        if datatree is None:
            await self.send_error("get_histogram: no file is open")
            return

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: histogram_data(datatree, variable, sweep, bins=int(bins)),
        )

        await self.send({"type": "histogram_result", **result})

    async def _handle_get_region_stats(self, msg: dict[str, Any]) -> None:
        """Compute statistics for a polar region."""
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        az_min = msg.get("az_min", 0.0)
        az_max = msg.get("az_max", 360.0)
        range_min = msg.get("range_min", 0.0)
        range_max = msg.get("range_max", 300000.0)

        if not variable or not isinstance(variable, str):
            await self.send_error("get_region_stats: missing or invalid 'variable'")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("get_region_stats: 'sweep' must be a non-negative integer")
            return

        datatree = _get_datatree(msg.get("file_id"))
        if datatree is None:
            await self.send_error("get_region_stats: no file is open")
            return

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: region_stats(
                datatree, variable, sweep,
                az_min=float(az_min),
                az_max=float(az_max),
                range_min=float(range_min),
                range_max=float(range_max),
            ),
        )

        await self.send({"type": "region_stats_result", **result})

    # ------------------------------------------------------------------
    # 3D volume & cross-section handlers
    # ------------------------------------------------------------------

    async def _handle_get_volume(self, msg: dict[str, Any]) -> None:
        """Extract a 3D Cartesian volume grid from all sweeps.

        Expected message::

            {type: "get_volume", variable: "DBZH",
             box: {x_min: -50000, x_max: 50000, y_min: -50000, y_max: 50000},
             resolution: 100, z_max: 15000, file_id: "..."}

        Response: JSON header followed by chunked binary Float32 data.
        """
        import numpy as np

        variable = msg.get("variable")
        box = msg.get("box")
        resolution = msg.get("resolution", 100)
        z_max_val = msg.get("z_max", 15000.0)
        file_id = msg.get("file_id")

        if not variable or not isinstance(variable, str):
            await self.send_error("get_volume: missing or invalid 'variable'")
            return
        if not isinstance(box, dict):
            await self.send_error("get_volume: 'box' must be an object with x_min, x_max, y_min, y_max")
            return
        for key in ("x_min", "x_max", "y_min", "y_max"):
            if key not in box or not isinstance(box[key], (int, float)):
                await self.send_error(f"get_volume: 'box.{key}' must be a number")
                return
        if not isinstance(resolution, int) or resolution < 2 or resolution > 500:
            await self.send_error("get_volume: 'resolution' must be an integer between 2 and 500")
            return
        if not isinstance(z_max_val, (int, float)) or z_max_val <= 0:
            await self.send_error("get_volume: 'z_max' must be a positive number")
            return

        reader = _get_reader(file_id)
        if reader.datatree is None:
            await self.send_error("get_volume: no file is open")
            return

        await self.send_progress(10, f"Extracting 3D volume for {variable}...")

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: extract_volume(
                    reader.datatree,
                    variable,
                    box,
                    resolution=resolution,
                    z_max=float(z_max_val),
                ),
            )
        except ValueError as exc:
            await self.send_error(f"get_volume: {exc}")
            return

        flat_data: np.ndarray = result.pop("data")
        val_bytes = flat_data.tobytes()

        CHUNK_SIZE = 900 * 1024
        chunks = [val_bytes[i : i + CHUNK_SIZE] for i in range(0, len(val_bytes), CHUNK_SIZE)]

        # Send JSON header
        await self.send({
            "type": "volume_data",
            **result,
            "chunks": len(chunks),
            "byte_length": len(val_bytes),
        })

        # Send binary chunks
        try:
            for chunk in chunks:
                await self.ws.send(chunk)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed while sending volume data")

    async def _handle_get_cross_section_3d(self, msg: dict[str, Any]) -> None:
        """Extract a vertical curtain cross-section along a Cartesian line.

        Expected message::

            {type: "get_cross_section_3d", variable: "DBZH",
             start: {x: 0, y: 0}, end: {x: 50000, y: 50000},
             width: 2000, file_id: "..."}

        Response: JSON header followed by chunked binary Float32 data.
        """
        import numpy as np

        variable = msg.get("variable")
        start = msg.get("start")
        end = msg.get("end")
        width = msg.get("width", 2000.0)
        file_id = msg.get("file_id")

        if not variable or not isinstance(variable, str):
            await self.send_error("get_cross_section_3d: missing or invalid 'variable'")
            return
        if not isinstance(start, dict) or "x" not in start or "y" not in start:
            await self.send_error("get_cross_section_3d: 'start' must be {x, y}")
            return
        if not isinstance(end, dict) or "x" not in end or "y" not in end:
            await self.send_error("get_cross_section_3d: 'end' must be {x, y}")
            return
        for pt_name, pt in [("start", start), ("end", end)]:
            for coord in ("x", "y"):
                if not isinstance(pt[coord], (int, float)):
                    await self.send_error(
                        f"get_cross_section_3d: '{pt_name}.{coord}' must be a number"
                    )
                    return
        if not isinstance(width, (int, float)) or width <= 0:
            await self.send_error("get_cross_section_3d: 'width' must be a positive number")
            return

        reader = _get_reader(file_id)
        if reader.datatree is None:
            await self.send_error("get_cross_section_3d: no file is open")
            return

        await self.send_progress(10, f"Extracting 3D cross-section for {variable}...")

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: extract_cross_section_3d(
                    reader.datatree,
                    variable,
                    start,
                    end,
                    width=float(width),
                ),
            )
        except ValueError as exc:
            await self.send_error(f"get_cross_section_3d: {exc}")
            return

        flat_data: np.ndarray = result.pop("data")
        val_bytes = flat_data.tobytes()

        CHUNK_SIZE = 900 * 1024
        chunks = [val_bytes[i : i + CHUNK_SIZE] for i in range(0, len(val_bytes), CHUNK_SIZE)]

        # Send JSON header
        await self.send({
            "type": "cross_section_3d_data",
            **result,
            "chunks": len(chunks),
            "byte_length": len(val_bytes),
        })

        # Send binary chunks
        try:
            for chunk in chunks:
                await self.ws.send(chunk)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed while sending cross-section 3D data")

    async def _handle_ping(self, msg: dict[str, Any]) -> None:
        """Health check handler — responds with pong and server status."""
        has_file = _get_datatree(msg.get("file_id")) is not None
        await self.send({
            "type": "pong",
            "status": "ok",
            "file_open": has_file,
        })

    async def _handle_open_file(self, msg: dict[str, Any]) -> None:
        global _last_file_id

        path = msg.get("path")
        if not path:
            await self.send_error("open_file: missing 'path'")
            return
        if not isinstance(path, str):
            await self.send_error("open_file: 'path' must be a string")
            return

        await self.send_progress(5, f"Detecting format for {Path(path).name}...")

        # Create a dedicated RadarReader for multi-file support
        file_id = str(uuid.uuid4())
        reader = RadarReader()

        _shared_renderer.invalidate_cache()
        loop = asyncio.get_running_loop()
        schema = await loop.run_in_executor(None, reader.open_file, path)

        # Store in multi-file dict and sync legacy shared reader
        _file_readers[file_id] = reader
        _last_file_id = file_id
        _shared_reader._dtree = reader._dtree
        _shared_reader._path = reader._path
        _shared_reader._format = reader._format

        await self.send({"type": "file_opened", "data": schema, "file_id": file_id})

        # Pre-render first variable / sweep 0 in background for instant display
        if schema.get("variables") and schema.get("sweeps"):
            first_var = schema["variables"][0]
            self._prerender_task = asyncio.create_task(
                self._prerender(first_var, 0, loop, file_id),
            )

    async def _handle_close_file(self, msg: dict[str, Any]) -> None:
        """Remove a file from the multi-file reader dict."""
        global _last_file_id

        file_id = msg.get("file_id")
        if not file_id or not isinstance(file_id, str):
            await self.send_error("close_file: missing or invalid 'file_id'")
            return

        removed = _file_readers.pop(file_id, None)
        if removed is None:
            logger.debug("close_file: file_id %s not found (already closed?)", file_id)

        # If we closed the last active file, update the pointer
        if _last_file_id == file_id:
            _last_file_id = next(iter(_file_readers), None)

        await self.send({"type": "file_closed", "file_id": file_id})
        logger.info("Closed file %s (%d files still open)", file_id, len(_file_readers))

    async def _prerender(
        self, variable: str, sweep: int, loop: asyncio.AbstractEventLoop, file_id: str | None = None
    ) -> None:
        """Pre-render a sweep in background so the first render request is instant."""
        try:
            reader = _get_reader(file_id)
            data = await loop.run_in_executor(
                None, reader.get_sweep_data, sweep, variable,
            )
            async with _render_lock:
                await loop.run_in_executor(
                    None, _shared_renderer.render_sweep, data, variable, sweep,
                )
            logger.info("Pre-rendered %s sweep %d", variable, sweep)
        except Exception:
            logger.debug("Pre-render failed (non-fatal)", exc_info=True)

    async def _handle_render(self, msg: dict[str, Any]) -> None:
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        width = msg.get("width", 800)
        height = msg.get("height", 600)
        bbox = msg.get("bbox")
        file_id = msg.get("file_id")

        if not variable:
            await self.send_error("render: missing 'variable'")
            return
        if not isinstance(variable, str):
            await self.send_error("render: 'variable' must be a string")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("render: 'sweep' must be a non-negative integer")
            return
        if not isinstance(width, int) or width <= 0 or width > 10000:
            await self.send_error("render: 'width' must be an integer between 1 and 10000")
            return
        if not isinstance(height, int) or height <= 0 or height > 10000:
            await self.send_error("render: 'height' must be an integer between 1 and 10000")
            return
        if bbox is not None:
            if not isinstance(bbox, list) or len(bbox) != 4:
                await self.send_error("render: 'bbox' must be a list of 4 numbers")
                return
            if not all(isinstance(v, (int, float)) for v in bbox):
                await self.send_error("render: 'bbox' values must be numbers")
                return

        await self.send_progress(10, f"Rendering {variable} sweep {sweep}...")

        loop = asyncio.get_running_loop()

        reader = _get_reader(file_id)
        data = await loop.run_in_executor(
            None, reader.get_sweep_data, sweep, variable
        )

        async with _render_lock:
            result = await loop.run_in_executor(
                None,
                _shared_renderer.render_sweep,
                data,
                variable,
                sweep,
                width,
                height,
                bbox,
            )

        await self.send({"type": "render_result", **result})

    async def _handle_get_sweep_data(self, msg: dict[str, Any]) -> None:
        """Send raw sweep data as binary for client-side WebGL rendering.

        The response is a JSON header followed by binary Float32Arrays:
        {type: "sweep_data", variable, sweep, n_az, n_range,
         azimuth_offset, range_offset, values_offset, vmin, vmax, units,
         byte_length}
        Then a single binary message: [azimuth f32] [range f32] [values f32]

        Optional ``file_id`` selects which open file to read from (defaults
        to the most recently opened file for backward compatibility).
        """
        import struct

        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        file_id = msg.get("file_id")

        if not variable or not isinstance(variable, str):
            await self.send_error("get_sweep_data: missing or invalid 'variable'")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("get_sweep_data: 'sweep' must be a non-negative integer")
            return

        reader = _get_reader(file_id)
        logger.info("get_sweep_data: variable=%s sweep=%d file_id=%s", variable, sweep, file_id)

        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, reader.get_sweep_data, sweep, variable,
        )

        import numpy as np

        azimuth = data.coords["azimuth"].values.astype(np.float32) if "azimuth" in data.coords else np.arange(data.shape[0], dtype=np.float32)
        range_m = data.coords["range"].values.astype(np.float32) if "range" in data.coords else np.arange(data.shape[-1], dtype=np.float32)
        values = np.nan_to_num(data.values.astype(np.float32), nan=-9999.0)

        # Pack into a single binary buffer: [azimuth][range][values]
        az_bytes = azimuth.tobytes()
        rng_bytes = range_m.tobytes()
        val_bytes = values.tobytes()

        buf = az_bytes + rng_bytes + val_bytes

        # Resolve colormap range
        from engine.renderer import _resolve_range
        vmin, vmax = _resolve_range(variable)
        if vmin is None:
            vmin = float(np.nanmin(data.values))
        if vmax is None:
            vmax = float(np.nanmax(data.values))

        units = str(data.attrs.get("units", data.attrs.get("unit", "")))

        # Chunk the buffer to stay under WKWebView's 1 MB WebSocket message limit.
        import math
        CHUNK_SIZE = 900 * 1024  # 900 KB — comfortably under the 1 MB WKWebView cap
        chunks = [buf[i : i + CHUNK_SIZE] for i in range(0, len(buf), CHUNK_SIZE)]
        n_chunks = len(chunks)

        # Send JSON header first
        await self.send({
            "type": "sweep_data",
            "variable": variable,
            "sweep": sweep,
            "n_az": len(azimuth),
            "n_range": len(range_m),
            "azimuth_bytes": len(az_bytes),
            "range_bytes": len(rng_bytes),
            "values_bytes": len(val_bytes),
            "vmin": vmin,
            "vmax": vmax,
            "units": units,
            "max_range": float(range_m.max()),
            "chunks": n_chunks,
        })

        # Send each chunk as a separate binary WebSocket message.
        try:
            for chunk in chunks:
                await self.ws.send(chunk)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed while sending binary sweep data")

    async def _handle_process(self, msg: dict[str, Any]) -> None:
        pipeline = msg.get("pipeline", {})
        file_id = msg.get("file_id")
        if not isinstance(pipeline, dict):
            await self.send_error("process: 'pipeline' must be an object")
            return

        reader = _get_reader(file_id)
        datatree = reader.datatree
        if datatree is None:
            await self.send_error("process: no file is open")
            return

        loop = asyncio.get_running_loop()

        def progress_sync(percent: int, message: str) -> None:
            asyncio.run_coroutine_threadsafe(
                self.send_progress(percent, message), loop
            )

        async with _mutation_lock:
            result_tree = await loop.run_in_executor(
                None,
                _shared_processor.process,
                datatree,
                pipeline,
                progress_sync,
            )

        reader._dtree = result_tree
        if reader is _shared_reader or file_id == _last_file_id:
            _shared_reader._dtree = result_tree

        await self.send_progress(100, "Processing complete")

    async def _handle_export(self, msg: dict[str, Any]) -> None:
        fmt = msg.get("format", "png")
        dpi = msg.get("dpi", 300)
        output_path = msg.get("path")
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        options = msg.get("options", {})
        file_id = msg.get("file_id")

        if not isinstance(fmt, str):
            await self.send_error("export: 'format' must be a string")
            return
        if not isinstance(dpi, int) or dpi <= 0 or dpi > 600:
            await self.send_error("export: 'dpi' must be an integer between 1 and 600")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("export: 'sweep' must be a non-negative integer")
            return
        if variable is not None and not isinstance(variable, str):
            await self.send_error("export: 'variable' must be a string")
            return
        if output_path is not None and not isinstance(output_path, str):
            await self.send_error("export: 'path' must be a string")
            return
        if not isinstance(options, dict):
            options = {}

        datatree = _get_datatree(file_id)
        if datatree is None:
            await self.send_error("export: no file is open")
            return

        await self.send_progress(10, f"Exporting as {fmt}...")

        loop = asyncio.get_running_loop()

        def progress_sync(percent: int, message: str) -> None:
            asyncio.run_coroutine_threadsafe(
                self.send_progress(percent, message), loop
            )

        async with _mutation_lock:
            saved_path = await loop.run_in_executor(
                None,
                lambda: _shared_exporter.export(
                    datatree,
                    fmt=fmt,
                    dpi=dpi,
                    output_path=output_path,
                    variable=variable,
                    sweep=sweep,
                    progress=progress_sync,
                    options=options,
                ),
            )

        await self.send({"type": "export_complete", "path": saved_path})

    async def _handle_batch_export(self, msg: dict[str, Any]) -> None:
        variables = msg.get("variables", [])
        sweeps = msg.get("sweeps", [])
        fmt = msg.get("format", "png")
        dpi = msg.get("dpi", 300)
        output_dir = msg.get("output_dir")
        options = msg.get("options", {})
        file_id = msg.get("file_id")

        if not isinstance(variables, list) or not all(isinstance(v, str) for v in variables):
            await self.send_error("batch_export: 'variables' must be a list of strings")
            return
        if not isinstance(sweeps, list) or not all(isinstance(s, int) for s in sweeps):
            await self.send_error("batch_export: 'sweeps' must be a list of integers")
            return
        if not isinstance(fmt, str):
            await self.send_error("batch_export: 'format' must be a string")
            return
        if not isinstance(dpi, int) or dpi <= 0 or dpi > 600:
            await self.send_error("batch_export: 'dpi' must be between 1 and 600")
            return
        if output_dir is not None and not isinstance(output_dir, str):
            await self.send_error("batch_export: 'output_dir' must be a string")
            return
        if not isinstance(options, dict):
            options = {}

        datatree = _get_datatree(file_id)
        if datatree is None:
            await self.send_error("batch_export: no file is open")
            return

        loop = asyncio.get_running_loop()

        def progress_sync(percent: int, message: str) -> None:
            asyncio.run_coroutine_threadsafe(
                self.send_progress(percent, message), loop
            )

        async with _mutation_lock:
            saved_paths = await loop.run_in_executor(
                None,
                lambda: _shared_exporter.batch_export(
                    datatree,
                    variables=variables,
                    sweeps=sweeps,
                    fmt=fmt,
                    dpi=dpi,
                    output_dir=output_dir,
                    progress=progress_sync,
                    options=options,
                ),
            )

        await self.send({
            "type": "batch_export_complete",
            "paths": saved_paths,
            "count": len(saved_paths),
            "output_dir": output_dir or (str(Path(saved_paths[0]).parent) if saved_paths else None),
        })

    async def _handle_export_animation(self, msg: dict[str, Any]) -> None:
        variable = msg.get("variable")
        sweeps = msg.get("sweeps", [])
        output_path = msg.get("path")
        frame_duration_ms = msg.get("frame_duration_ms", 500)
        dpi = msg.get("dpi", 150)
        options = msg.get("options", {})
        file_id = msg.get("file_id")

        if not variable or not isinstance(variable, str):
            await self.send_error("export_animation: 'variable' must be a non-empty string")
            return
        if not isinstance(sweeps, list) or not all(isinstance(s, int) for s in sweeps):
            await self.send_error("export_animation: 'sweeps' must be a list of integers")
            return
        if not sweeps:
            await self.send_error("export_animation: 'sweeps' must not be empty")
            return
        if not isinstance(frame_duration_ms, int) or frame_duration_ms < 50:
            await self.send_error("export_animation: 'frame_duration_ms' must be >= 50")
            return
        if not isinstance(dpi, int) or dpi <= 0 or dpi > 600:
            await self.send_error("export_animation: 'dpi' must be between 1 and 600")
            return
        if output_path is not None and not isinstance(output_path, str):
            await self.send_error("export_animation: 'path' must be a string")
            return
        if not isinstance(options, dict):
            options = {}

        datatree = _get_datatree(file_id)
        if datatree is None:
            await self.send_error("export_animation: no file is open")
            return

        loop = asyncio.get_running_loop()

        def progress_sync(percent: int, message: str) -> None:
            asyncio.run_coroutine_threadsafe(
                self.send_progress(percent, message), loop
            )

        async with _mutation_lock:
            saved_path = await loop.run_in_executor(
                None,
                lambda: _shared_exporter.export_animation(
                    datatree,
                    variable=variable,
                    sweeps=sweeps,
                    output_path=output_path,
                    frame_duration_ms=frame_duration_ms,
                    dpi=dpi,
                    progress=progress_sync,
                    options=options,
                ),
            )

        await self.send({"type": "animation_export_complete", "path": saved_path})

    # ------------------------------------------------------------------
    # Retrieval handlers
    # ------------------------------------------------------------------

    async def _handle_run_retrieval(self, msg: dict[str, Any]) -> None:
        """Run a meteorological retrieval algorithm on the current data."""
        name = msg.get("name")
        if not name or not isinstance(name, str):
            await self.send_error("run_retrieval: missing or invalid 'name'")
            return

        sweep = msg.get("sweep", 0)
        params = msg.get("params", {})
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("run_retrieval: 'sweep' must be a non-negative integer")
            return
        if not isinstance(params, dict):
            await self.send_error("run_retrieval: 'params' must be an object")
            return

        datatree = _get_datatree(msg.get("file_id"))
        if datatree is None:
            await self.send_error("run_retrieval: no file is open")
            return

        await self.send_progress(10, f"Running retrieval: {name}...")

        loop = asyncio.get_running_loop()
        result_da = await loop.run_in_executor(
            None, run_retrieval, datatree, name, sweep, params,
        )

        import numpy as np

        values = result_da.values.astype(np.float32)
        valid = values[np.isfinite(values)]
        stats: dict[str, Any] = {}
        if len(valid) > 0:
            stats = {
                "min": float(np.min(valid)),
                "max": float(np.max(valid)),
                "mean": float(np.mean(valid)),
                "std": float(np.std(valid)),
            }

        await self.send({
            "type": "retrieval_result",
            "name": name,
            "sweep": sweep,
            "shape": list(values.shape),
            "units": str(result_da.attrs.get("units", "")),
            "long_name": str(result_da.attrs.get("long_name", name)),
            "stats": stats,
        })

        # Send the data as binary (chunked like sweep_data)
        val_bytes = np.nan_to_num(values, nan=-9999.0).tobytes()
        CHUNK_SIZE = 900 * 1024
        chunks = [val_bytes[i : i + CHUNK_SIZE] for i in range(0, len(val_bytes), CHUNK_SIZE)]

        await self.send({
            "type": "retrieval_data",
            "name": name,
            "sweep": sweep,
            "n_az": values.shape[0] if values.ndim >= 1 else 1,
            "n_range": values.shape[1] if values.ndim >= 2 else values.shape[0],
            "chunks": len(chunks),
            "byte_length": len(val_bytes),
        })

        try:
            for chunk in chunks:
                await self.ws.send(chunk)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed while sending retrieval data")

    async def _handle_list_retrievals(self, msg: dict[str, Any]) -> None:
        """List available retrievals with their metadata."""
        retrievals = []
        for name, entry in RETRIEVAL_REGISTRY.items():
            retrievals.append({
                "name": name,
                "description": entry["description"],
                "required_variables": entry["required_variables"],
                "params": {
                    k: {"type": v["type"], "default": v["default"], "label": v["label"]}
                    for k, v in entry["params"].items()
                },
            })
        await self.send({"type": "retrievals_list", "retrievals": retrievals})

    # ------------------------------------------------------------------
    # Cross-section and vertical profile handlers
    # ------------------------------------------------------------------

    async def _handle_get_cross_section(self, msg: dict[str, Any]) -> None:
        """Extract an RHI-like cross-section between two geographic points."""
        start = msg.get("start")
        end = msg.get("end")
        variable = msg.get("variable")
        n_points = msg.get("n_points", 200)

        if not variable or not isinstance(variable, str):
            await self.send_error("get_cross_section: missing or invalid 'variable'")
            return
        if not isinstance(start, list) or len(start) != 2:
            await self.send_error("get_cross_section: 'start' must be [lat, lon]")
            return
        if not isinstance(end, list) or len(end) != 2:
            await self.send_error("get_cross_section: 'end' must be [lat, lon]")
            return
        if not all(isinstance(v, (int, float)) for v in start + end):
            await self.send_error("get_cross_section: coordinates must be numbers")
            return

        datatree = _get_datatree(msg.get("file_id"))
        if datatree is None:
            await self.send_error("get_cross_section: no file is open")
            return

        await self.send_progress(10, "Extracting cross-section...")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            extract_cross_section,
            datatree,
            tuple(start),
            tuple(end),
            variable,
        )

        await self.send({"type": "cross_section_result", **result})

    async def _handle_get_vertical_profile(self, msg: dict[str, Any]) -> None:
        """Extract a vertical profile at a geographic point."""
        lat = msg.get("lat")
        lon = msg.get("lon")
        variable = msg.get("variable")

        if not variable or not isinstance(variable, str):
            await self.send_error("get_vertical_profile: missing or invalid 'variable'")
            return
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            await self.send_error("get_vertical_profile: 'lat' and 'lon' must be numbers")
            return

        datatree = _get_datatree(msg.get("file_id"))
        if datatree is None:
            await self.send_error("get_vertical_profile: no file is open")
            return

        await self.send_progress(10, "Extracting vertical profile...")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            extract_vertical_profile,
            datatree,
            float(lat),
            float(lon),
            variable,
        )

        await self.send({"type": "vertical_profile_result", **result})

    # ------------------------------------------------------------------
    # Statistics handler
    # ------------------------------------------------------------------

    async def _handle_get_statistics(self, msg: dict[str, Any]) -> None:
        """Compute statistics for a variable/sweep."""
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        stat_type = msg.get("stat_type", "histogram")
        params = msg.get("params", {})

        if not variable or not isinstance(variable, str):
            await self.send_error("get_statistics: missing or invalid 'variable'")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("get_statistics: 'sweep' must be a non-negative integer")
            return
        if not isinstance(stat_type, str):
            await self.send_error("get_statistics: 'stat_type' must be a string")
            return

        datatree = _get_datatree(msg.get("file_id"))
        if datatree is None:
            await self.send_error("get_statistics: no file is open")
            return

        await self.send_progress(10, f"Computing {stat_type}...")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            get_statistics,
            datatree,
            variable,
            sweep,
            stat_type,
            params,
        )

        await self.send({"type": "statistics_result", "stat_type": stat_type, **result})

    # ------------------------------------------------------------------
    # QC pipeline handlers
    # ------------------------------------------------------------------

    async def _handle_run_qc(self, msg: dict[str, Any]) -> None:
        """Run a QC pipeline on a variable/sweep and return cleaned data.

        Expected message::

            {type: "run_qc",
             steps: [{name: "despeckle", params: {window_size: 3}}, ...],
             variable: "DBZH", sweep: 0}

        Response: same chunked binary protocol as ``get_sweep_data``.
        """
        import struct

        steps = msg.get("steps", [])
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)

        if not variable or not isinstance(variable, str):
            await self.send_error("run_qc: missing or invalid 'variable'")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("run_qc: 'sweep' must be a non-negative integer")
            return
        if not isinstance(steps, list) or len(steps) == 0:
            await self.send_error("run_qc: 'steps' must be a non-empty list")
            return

        reader = _get_reader(msg.get("file_id"))
        datatree = reader.datatree
        if datatree is None:
            await self.send_error("run_qc: no file is open")
            return

        await self.send_progress(5, "Building QC pipeline...")

        # Build pipeline
        try:
            pipeline = QCPipeline.from_dict(steps)
        except ValueError as exc:
            await self.send_error(f"run_qc: {exc}")
            return

        loop = asyncio.get_running_loop()

        await self.send_progress(15, f"Reading {variable} sweep {sweep}...")
        data = await loop.run_in_executor(
            None, reader.get_sweep_data, sweep, variable,
        )

        await self.send_progress(30, f"Running {len(pipeline)} QC steps...")

        cleaned = await loop.run_in_executor(None, pipeline.run, data)

        await self.send_progress(80, "Sending QC results...")

        import numpy as np

        azimuth = cleaned.coords["azimuth"].values.astype(np.float32) if "azimuth" in cleaned.coords else np.arange(cleaned.shape[0], dtype=np.float32)
        range_m = cleaned.coords["range"].values.astype(np.float32) if "range" in cleaned.coords else np.arange(cleaned.shape[-1], dtype=np.float32)
        values = np.nan_to_num(cleaned.values.astype(np.float32), nan=-9999.0)

        az_bytes = azimuth.tobytes()
        rng_bytes = range_m.tobytes()
        val_bytes = values.tobytes()
        buf = az_bytes + rng_bytes + val_bytes

        from engine.renderer import _resolve_range
        vmin, vmax = _resolve_range(variable)
        if vmin is None:
            vmin = float(np.nanmin(cleaned.values))
        if vmax is None:
            vmax = float(np.nanmax(cleaned.values))

        units = str(cleaned.attrs.get("units", cleaned.attrs.get("unit", "")))

        import math
        CHUNK_SIZE = 900 * 1024
        chunks = [buf[i : i + CHUNK_SIZE] for i in range(0, len(buf), CHUNK_SIZE)]

        await self.send({
            "type": "qc_result",
            "variable": variable,
            "sweep": sweep,
            "n_az": len(azimuth),
            "n_range": len(range_m),
            "azimuth_bytes": len(az_bytes),
            "range_bytes": len(rng_bytes),
            "values_bytes": len(val_bytes),
            "vmin": vmin,
            "vmax": vmax,
            "units": units,
            "max_range": float(range_m.max()),
            "chunks": len(chunks),
            "steps_applied": pipeline.to_dict(),
        })

        try:
            for chunk in chunks:
                await self.ws.send(chunk)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed while sending QC data")

        await self.send_progress(100, "QC pipeline complete")

    async def _handle_list_qc_algorithms(self, msg: dict[str, Any]) -> None:
        """Return the QC algorithm registry with descriptions and parameter metadata."""
        algorithms = []
        for name, entry in QC_REGISTRY.items():
            algorithms.append({
                "name": name,
                "description": entry["description"],
                "params": {
                    k: {
                        "type": v["type"],
                        "default": v["default"],
                        "label": v["label"],
                        **({"min": v["min"]} if "min" in v else {}),
                        **({"max": v["max"]} if "max" in v else {}),
                        **({"options": v["options"]} if "options" in v else {}),
                    }
                    for k, v in entry["params"].items()
                },
                "applicable_variables": entry["applicable_variables"],
            })
        await self.send({"type": "qc_algorithms_list", "algorithms": algorithms})

    # ------------------------------------------------------------------
    # Temporal analysis handlers
    # ------------------------------------------------------------------

    async def _handle_get_time_series(self, msg: dict[str, Any]) -> None:
        """Extract value at a point across multiple files."""
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        azimuth = msg.get("azimuth")
        range_m = msg.get("range_m")
        file_ids = msg.get("file_ids", [])

        if not variable or not isinstance(variable, str):
            await self.send_error("get_time_series: missing or invalid 'variable'")
            return
        if not isinstance(azimuth, (int, float)) or not isinstance(range_m, (int, float)):
            await self.send_error("get_time_series: 'azimuth' and 'range_m' must be numbers")
            return
        if not isinstance(file_ids, list) or len(file_ids) == 0:
            await self.send_error("get_time_series: 'file_ids' must be a non-empty list")
            return

        readers: dict[str, RadarReader] = {}
        for fid in file_ids:
            if fid in _file_readers:
                readers[fid] = _file_readers[fid]

        if not readers:
            await self.send_error("get_time_series: none of the specified file_ids are open")
            return

        await self.send_progress(10, "Computing time series...")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, compute_time_series, readers, variable, sweep,
            float(azimuth), float(range_m),
        )

        await self.send({"type": "time_series_result", **result})

    async def _handle_get_difference(self, msg: dict[str, Any]) -> None:
        """Compute element-wise difference between two files."""
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        fid1 = msg.get("file_id_1")
        fid2 = msg.get("file_id_2")

        if not variable or not isinstance(variable, str):
            await self.send_error("get_difference: missing or invalid 'variable'")
            return
        if not fid1 or not fid2:
            await self.send_error("get_difference: 'file_id_1' and 'file_id_2' are required")
            return

        reader1 = _file_readers.get(fid1)
        reader2 = _file_readers.get(fid2)
        if not reader1:
            await self.send_error(f"get_difference: file_id_1 '{fid1}' not found")
            return
        if not reader2:
            await self.send_error(f"get_difference: file_id_2 '{fid2}' not found")
            return

        await self.send_progress(10, "Computing difference...")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, compute_difference, reader1, reader2, variable, sweep,
        )

        diff_bytes = result.pop("diff_values")
        az_bytes_raw = result.pop("azimuth")
        rng_bytes_raw = result.pop("range")

        await self.send({"type": "difference_result", **result})

        buf = az_bytes_raw + rng_bytes_raw + diff_bytes
        CHUNK_SIZE = 900 * 1024
        chunks = [buf[i : i + CHUNK_SIZE] for i in range(0, len(buf), CHUNK_SIZE)]

        try:
            for chunk in chunks:
                await self.ws.send(chunk)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed while sending difference data")

    async def _handle_get_accumulation(self, msg: dict[str, Any]) -> None:
        """Accumulate a variable across multiple files."""
        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        file_ids = msg.get("file_ids", [])
        method = msg.get("method", "sum")

        if not variable or not isinstance(variable, str):
            await self.send_error("get_accumulation: missing or invalid 'variable'")
            return
        if not isinstance(file_ids, list) or len(file_ids) == 0:
            await self.send_error("get_accumulation: 'file_ids' must be a non-empty list")
            return
        if method not in ("sum", "mean", "max"):
            await self.send_error("get_accumulation: 'method' must be sum, mean, or max")
            return

        readers: dict[str, RadarReader] = {}
        for fid in file_ids:
            if fid in _file_readers:
                readers[fid] = _file_readers[fid]

        if not readers:
            await self.send_error("get_accumulation: none of the specified file_ids are open")
            return

        await self.send_progress(10, f"Computing {method} accumulation...")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, compute_accumulation, readers, variable, sweep, method,
        )

        await self.send({"type": "accumulation_result", **result})

    # ------------------------------------------------------------------
    # Storm cell identification & tracking
    # ------------------------------------------------------------------

    async def _handle_identify_cells(self, msg: dict[str, Any]) -> None:
        """Identify storm cells in a reflectivity sweep.

        Expected message::

            {type: "identify_cells", variable: "DBZH", sweep: 0,
             params: {threshold_dbz: 35, min_size_km2: 10}}

        Response::

            {type: "cells_identified", variable, sweep, cells: [...]}
        """
        variable = msg.get("variable", "DBZH")
        sweep_idx = msg.get("sweep", 0)
        params = msg.get("params", {})

        if not isinstance(variable, str):
            await self.send_error("identify_cells: 'variable' must be a string")
            return
        if not isinstance(sweep_idx, int) or sweep_idx < 0:
            await self.send_error(
                "identify_cells: 'sweep' must be a non-negative integer"
            )
            return

        reader = _get_reader(msg.get("file_id"))
        datatree = reader.datatree
        if datatree is None:
            await self.send_error("identify_cells: no file is open")
            return

        threshold_dbz = float(params.get("threshold_dbz", 35.0))
        min_size_km2 = float(params.get("min_size_km2", 10.0))

        await self.send_progress(
            10, f"Identifying cells in {variable} sweep {sweep_idx}..."
        )

        loop = asyncio.get_running_loop()

        data = await loop.run_in_executor(
            None, reader.get_sweep_data, sweep_idx, variable,
        )

        cells = await loop.run_in_executor(
            None, identify_cells, data, threshold_dbz, min_size_km2,
        )

        # Store for subsequent tracking
        self._previous_cells = cells

        await self.send_progress(100, f"Found {len(cells)} cells")
        await self.send({
            "type": "cells_identified",
            "variable": variable,
            "sweep": sweep_idx,
            "threshold_dbz": threshold_dbz,
            "min_size_km2": min_size_km2,
            "cells": [c.to_dict() for c in cells],
        })

    async def _handle_track_cells(self, msg: dict[str, Any]) -> None:
        """Track cells between the previous identification and a new sweep.

        Expected message::

            {type: "track_cells", variable: "DBZH", sweep: 1,
             params: {max_distance_km: 30, dt_seconds: 300,
                      threshold_dbz: 35, min_size_km2: 10}}

        Response::

            {type: "cells_tracked", tracks: [...], cells: [...]}
        """
        variable = msg.get("variable", "DBZH")
        sweep_idx = msg.get("sweep", 0)
        params = msg.get("params", {})

        if not isinstance(variable, str):
            await self.send_error("track_cells: 'variable' must be a string")
            return

        reader = _get_reader(msg.get("file_id"))
        datatree = reader.datatree
        if datatree is None:
            await self.send_error("track_cells: no file is open")
            return

        if not hasattr(self, "_previous_cells") or not self._previous_cells:
            await self.send_error(
                "track_cells: no previous cells — run identify_cells first"
            )
            return

        threshold_dbz = float(params.get("threshold_dbz", 35.0))
        min_size_km2 = float(params.get("min_size_km2", 10.0))
        max_distance_km = float(params.get("max_distance_km", 30.0))
        dt_seconds = float(params.get("dt_seconds", 300.0))

        await self.send_progress(
            10, f"Tracking cells in {variable} sweep {sweep_idx}..."
        )

        loop = asyncio.get_running_loop()

        data = await loop.run_in_executor(
            None, reader.get_sweep_data, sweep_idx, variable,
        )

        new_cells = await loop.run_in_executor(
            None, identify_cells, data, threshold_dbz, min_size_km2,
        )

        tracks = await loop.run_in_executor(
            None, track_cells, self._previous_cells, new_cells,
            max_distance_km, dt_seconds,
        )

        self._previous_cells = new_cells

        await self.send_progress(100, f"Tracked {len(tracks)} cells")
        await self.send({
            "type": "cells_tracked",
            "variable": variable,
            "sweep": sweep_idx,
            "cells": [c.to_dict() for c in new_cells],
            "tracks": [t.to_dict() for t in tracks],
        })

    # ------------------------------------------------------------------
    # Data table handler — windowed azimuth x range grid
    # ------------------------------------------------------------------

    async def _handle_get_data_table(self, msg: dict[str, Any]) -> None:
        """Return a windowed 2D slice of radar data for the spreadsheet viewer.

        Expected message::

            {type: "get_data_table", variable: "DBZH", sweep: 0,
             az_start: 0, az_end: 100, range_start: 0, range_end: 100}

        Response::

            {type: "data_table_result", azimuths: [...], ranges: [...],
             values: [[...], ...], total_az: N, total_range: M}
        """
        import numpy as np

        variable = msg.get("variable")
        sweep = msg.get("sweep", 0)
        az_start = msg.get("az_start", 0)
        az_end = msg.get("az_end", 360)
        range_start = msg.get("range_start", 0)
        range_end = msg.get("range_end", 1000)

        if not variable or not isinstance(variable, str):
            await self.send_error("get_data_table: missing or invalid 'variable'")
            return
        if not isinstance(sweep, int) or sweep < 0:
            await self.send_error("get_data_table: 'sweep' must be a non-negative integer")
            return

        reader = _get_reader(msg.get("file_id"))
        if reader.datatree is None:
            await self.send_error("get_data_table: no file is open")
            return

        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, reader.get_sweep_data, sweep, variable,
        )

        # Extract coordinate arrays
        azimuth = data.coords["azimuth"].values if "azimuth" in data.coords else np.arange(data.shape[0], dtype=np.float64)
        range_m = data.coords["range"].values if "range" in data.coords else np.arange(data.shape[-1], dtype=np.float64)
        vals = data.values

        total_az = len(azimuth)
        total_range = len(range_m)

        # Clamp window indices
        az_s = max(0, min(int(az_start), total_az))
        az_e = max(az_s, min(int(az_end), total_az))
        rng_s = max(0, min(int(range_start), total_range))
        rng_e = max(rng_s, min(int(range_end), total_range))

        sliced_az = azimuth[az_s:az_e].tolist()
        sliced_rng = range_m[rng_s:rng_e].tolist()
        sliced_vals = vals[az_s:az_e, rng_s:rng_e]

        # Replace NaN with None-friendly sentinel for JSON
        out_vals = []
        for row in sliced_vals:
            out_row = []
            for v in row:
                if np.isnan(v) or np.isinf(v):
                    out_row.append(None)
                else:
                    out_row.append(round(float(v), 6))
            out_vals.append(out_row)

        await self.send({
            "type": "data_table_result",
            "azimuths": [round(float(a), 4) for a in sliced_az],
            "ranges": [round(float(r), 4) for r in sliced_rng],
            "values": out_vals,
            "total_az": total_az,
            "total_range": total_range,
        })

    # ------------------------------------------------------------------
    # Metadata tree handler — full DataTree structure as nested dict
    # ------------------------------------------------------------------

    async def _handle_get_metadata_tree(self, msg: dict[str, Any]) -> None:
        """Return the full DataTree structure as a nested metadata tree.

        Response::

            {type: "metadata_tree_result",
             tree: [{name, type, children, dtype, shape, ...}, ...]}
        """
        import numpy as np

        reader = _get_reader(msg.get("file_id"))
        if reader.datatree is None:
            await self.send_error("get_metadata_tree: no file is open")
            return

        dtree = reader.datatree

        def _build_node(name: str, node) -> dict[str, Any]:
            """Recursively build a metadata node from a DataTree node."""
            result: dict[str, Any] = {
                "name": name,
                "type": "group",
                "children": [],
            }

            try:
                ds = node.to_dataset()
            except (ValueError, KeyError):
                return result

            # Coordinates
            for coord_name in sorted(ds.coords):
                coord = ds.coords[coord_name]
                child: dict[str, Any] = {
                    "name": str(coord_name),
                    "type": "coordinate",
                    "dtype": str(coord.dtype),
                    "shape": list(coord.shape),
                    "dims": [str(d) for d in coord.dims],
                }
                if coord.attrs.get("units"):
                    child["units"] = str(coord.attrs["units"])
                if coord.attrs.get("long_name"):
                    child["long_name"] = str(coord.attrs["long_name"])
                # Compute stats for numeric coords
                if coord.dtype.kind in ("f", "i", "u") and coord.size > 0:
                    try:
                        vals = coord.values
                        valid = vals[np.isfinite(vals)] if vals.dtype.kind == "f" else vals
                        if len(valid) > 0:
                            child["min"] = round(float(np.min(valid)), 6)
                            child["max"] = round(float(np.max(valid)), 6)
                            child["mean"] = round(float(np.mean(valid)), 6)
                    except Exception:
                        pass
                result["children"].append(child)

            # Data variables
            for var_name in sorted(ds.data_vars):
                da = ds[var_name]
                child = {
                    "name": str(var_name),
                    "type": "variable",
                    "dtype": str(da.dtype),
                    "shape": list(da.shape),
                    "dims": [str(d) for d in da.dims],
                }
                if da.attrs.get("units"):
                    child["units"] = str(da.attrs["units"])
                if da.attrs.get("long_name"):
                    child["long_name"] = str(da.attrs["long_name"])
                # Stats for float variables
                if da.dtype.kind == "f" and da.ndim >= 1 and da.size > 0:
                    try:
                        vals = da.values
                        valid = vals[np.isfinite(vals)]
                        if len(valid) > 0:
                            child["min"] = round(float(np.min(valid)), 4)
                            child["max"] = round(float(np.max(valid)), 4)
                            child["mean"] = round(float(np.mean(valid)), 4)
                    except Exception:
                        pass
                # Variable-level attributes as children
                attr_children = []
                for ak, av in da.attrs.items():
                    attr_children.append({
                        "name": str(ak),
                        "type": "attribute",
                        "value": _serializable(av),
                    })
                if attr_children:
                    child["children"] = attr_children
                result["children"].append(child)

            # Dataset-level attributes
            for ak, av in ds.attrs.items():
                result["children"].append({
                    "name": str(ak),
                    "type": "attribute",
                    "value": _serializable(av),
                })

            # Recurse into children
            if hasattr(node, "children"):
                for child_name in sorted(node.children):
                    child_node = node[child_name]
                    result["children"].append(_build_node(child_name, child_node))

            return result

        loop = asyncio.get_running_loop()
        tree_data = await loop.run_in_executor(
            None, _build_node, "root", dtree,
        )

        await self.send({
            "type": "metadata_tree_result",
            "tree": tree_data.get("children", []),
        })


async def _handle_connection(ws: ServerConnection) -> None:
    remote = ws.remote_address
    if not _origin_allowed(ws):
        request = getattr(ws, "request", None)
        headers = getattr(request, "headers", None)
        origin = headers.get("Origin") if headers is not None else None
        logger.warning("Rejected WebSocket connection from %s with origin %s", remote, origin)
        await ws.close(code=1008, reason="Origin not allowed")
        return

    logger.info("Client connected from %s", remote)
    handler = ConnectionHandler(ws)

    try:
        async for raw_message in ws:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8", errors="replace")
            await handler.handle_message(raw_message)
    except websockets.exceptions.ConnectionClosed as exc:
        logger.info("Client %s disconnected: %s", remote, exc)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Protocol error from %s: %s", remote, exc)
    except Exception as exc:
        logger.error(
            "Unexpected error for %s: %s\n%s", remote, exc, traceback.format_exc()
        )
    finally:
        logger.info("Connection from %s closed", remote)


async def _run_server(port: int) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    async with websockets.serve(
        _handle_connection,
        "localhost",
        port,
        max_size=WS_MAX_SIZE,
        ping_interval=30,
        ping_timeout=60,
    ):
        logger.info("xradar-desktop engine listening on ws://localhost:%d", port)
        print(f"READY:{port}", flush=True)
        await stop_event.wait()

    logger.info("Server shut down cleanly")


def _json_default(obj: Any) -> Any:
    import numpy as np

    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="xradar-desktop engine server")
    parser.add_argument("--port", type=int, required=True, help="WebSocket port")
    args = parser.parse_args()
    port = args.port

    if not (1024 <= port <= 65535):
        print(f"Port must be between 1024 and 65535, got {port}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(_run_server(port))
    except KeyboardInterrupt:
        logger.info("Interrupted — exiting")


if __name__ == "__main__":
    main()
