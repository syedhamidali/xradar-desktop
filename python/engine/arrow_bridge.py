"""Arrow IPC bridge — convert xarray objects to Arrow IPC bytes.

This module provides a bridge between xarray/Dask and Apache Arrow for
efficient data transfer.  Phase 1 uses standard serialization; future
phases will add zero-copy optimizations.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc
import xarray as xr

logger = logging.getLogger(__name__)


def dataset_to_arrow(ds: xr.Dataset) -> bytes:
    """Convert an xarray Dataset to Arrow IPC (stream format) bytes.

    Each data variable becomes a column in the resulting Arrow RecordBatch.
    Coordinate arrays are included as additional columns with a ``__coord_``
    prefix to avoid name collisions.

    Parameters
    ----------
    ds : xr.Dataset
        The dataset to serialise.

    Returns
    -------
    bytes
        Arrow IPC stream bytes.
    """
    arrays: dict[str, pa.Array] = {}

    # Flatten all data variables
    for name in ds.data_vars:
        arr = _to_flat_arrow(ds[name].values, str(name))
        if arr is not None:
            arrays[str(name)] = arr

    # Include coordinates
    for name in ds.coords:
        arr = _to_flat_arrow(ds.coords[name].values, str(name))
        if arr is not None:
            col_name = f"__coord_{name}"
            arrays[col_name] = arr

    if not arrays:
        logger.warning("dataset_to_arrow: no columns produced — returning empty batch")
        schema = pa.schema([])
        batch = pa.record_batch([], schema=schema)
    else:
        # All columns must have the same length; pad shorter ones with nulls
        max_len = max(len(a) for a in arrays.values())
        padded: dict[str, pa.Array] = {}
        for col_name, arr in arrays.items():
            if len(arr) < max_len:
                # Use zero-copy: convert arrow array to numpy, then pad
                existing = arr.to_numpy(zero_copy_only=False)
                padded_np = np.empty(max_len, dtype=existing.dtype)
                padded_np[: len(arr)] = existing
                padded_np[len(arr) :] = np.nan
                # Build validity mask for null padding
                mask = np.ones(max_len, dtype=bool)
                mask[len(arr) :] = False
                padded[col_name] = pa.array(padded_np, mask=~mask)
            else:
                padded[col_name] = arr
        batch = pa.record_batch(padded)

    return _batch_to_ipc_bytes(batch)


def dataarray_to_arrow(da: xr.DataArray) -> bytes:
    """Convert an xarray DataArray to Arrow IPC (stream format) bytes.

    The DataArray values are stored in a column named after ``da.name``
    (or ``"data"`` if unnamed).  Coordinate arrays are included as
    additional columns.

    Parameters
    ----------
    da : xr.DataArray
        The DataArray to serialise.

    Returns
    -------
    bytes
        Arrow IPC stream bytes.
    """
    col_name = str(da.name) if da.name else "data"
    arrays: dict[str, pa.Array] = {}

    arr = _to_flat_arrow(da.values, col_name)
    if arr is not None:
        arrays[col_name] = arr

    for coord_name in da.coords:
        coord_arr = _to_flat_arrow(da.coords[coord_name].values, str(coord_name))
        if coord_arr is not None:
            arrays[f"__coord_{coord_name}"] = coord_arr

    if not arrays:
        schema = pa.schema([])
        batch = pa.record_batch([], schema=schema)
    else:
        max_len = max(len(a) for a in arrays.values())
        padded: dict[str, pa.Array] = {}
        for name, a in arrays.items():
            if len(a) < max_len:
                extended = np.full(max_len, np.nan)
                extended[: len(a)] = a.to_numpy()
                padded[name] = pa.array(extended)
            else:
                padded[name] = a
        batch = pa.record_batch(padded)

    return _batch_to_ipc_bytes(batch)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_flat_arrow(values: Any, name: str) -> pa.Array | None:
    """Flatten an ndarray and convert to a pyarrow Array.

    Returns ``None`` if the array cannot be converted (e.g. object dtype
    with non-serialisable elements).
    """
    arr = np.asarray(values)

    # Flatten multi-dimensional arrays
    flat = arr.ravel()

    try:
        return pa.array(flat)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        logger.debug(
            "Could not convert '%s' (dtype=%s) to Arrow: %s", name, flat.dtype, exc
        )
        # Try converting to float64 as a fallback
        try:
            return pa.array(flat.astype(np.float64))
        except (
            pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError,
            ValueError, TypeError,
        ):
            logger.warning("Skipping column '%s' — cannot convert to Arrow", name)
            return None


def _batch_to_ipc_bytes(batch: pa.RecordBatch) -> bytes:
    """Serialise a RecordBatch to Arrow IPC stream format bytes."""
    sink = io.BytesIO()
    writer = ipc.new_stream(sink, batch.schema)
    writer.write_batch(batch)
    writer.close()
    return sink.getvalue()
