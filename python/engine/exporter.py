"""Publication-quality radar data exporter — matplotlib figures + data formats."""

from __future__ import annotations

import io
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import xarray as xr

matplotlib.use("Agg")  # Non-interactive backend

import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

# --- Export constraints ---
MIN_DPI = 72
MAX_DPI = 600
DEFAULT_DPI = 300
DEFAULT_FIG_SIZE = 10.0  # inches
DEFAULT_FONT_SIZE = 12
DEFAULT_IMAGE_FORMAT = "png"

ProgressCallback = Callable[[int, str], None]


def _noop_progress(percent: int, message: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Variable metadata: colormaps, ranges, units
# ---------------------------------------------------------------------------

_VARIABLE_CMAPS: dict[str, str] = {
    "DBZH": "pyart_NWSRef",
    "DBZ": "pyart_NWSRef",
    "reflectivity": "pyart_NWSRef",
    "corrected_reflectivity": "pyart_NWSRef",
    "total_power": "pyart_NWSRef",
    "VRADH": "pyart_NWSVel",
    "VEL": "pyart_NWSVel",
    "velocity": "pyart_NWSVel",
    "corrected_velocity": "pyart_NWSVel",
    "WRADH": "pyart_NWS_SPW",
    "WIDTH": "pyart_NWS_SPW",
    "spectrum_width": "pyart_NWS_SPW",
    "ZDR": "pyart_RefDiff",
    "differential_reflectivity": "pyart_RefDiff",
    "RHOHV": "pyart_RefDiff",
    "cross_correlation_ratio": "pyart_RefDiff",
    "PHIDP": "pyart_PhaseField",
    "differential_phase": "pyart_PhaseField",
    "KDP": "pyart_RefDiff",
    "specific_differential_phase": "pyart_RefDiff",
}

_VARIABLE_RANGES: dict[str, tuple[float, float]] = {
    "DBZH": (-20.0, 75.0),
    "DBZ": (-20.0, 75.0),
    "reflectivity": (-20.0, 75.0),
    "corrected_reflectivity": (-20.0, 75.0),
    "total_power": (-20.0, 75.0),
    "VRADH": (-30.0, 30.0),
    "VEL": (-30.0, 30.0),
    "velocity": (-30.0, 30.0),
    "corrected_velocity": (-30.0, 30.0),
    "WRADH": (0.0, 10.0),
    "WIDTH": (0.0, 10.0),
    "spectrum_width": (0.0, 10.0),
    "ZDR": (-1.0, 5.0),
    "differential_reflectivity": (-1.0, 5.0),
    "RHOHV": (0.0, 1.05),
    "cross_correlation_ratio": (0.0, 1.05),
    "PHIDP": (0.0, 180.0),
    "differential_phase": (0.0, 180.0),
    "KDP": (-2.0, 6.0),
    "specific_differential_phase": (-2.0, 6.0),
}

_VARIABLE_UNITS: dict[str, str] = {
    "DBZH": "dBZ",
    "DBZ": "dBZ",
    "reflectivity": "dBZ",
    "corrected_reflectivity": "dBZ",
    "total_power": "dBZ",
    "VRADH": "m/s",
    "VEL": "m/s",
    "velocity": "m/s",
    "corrected_velocity": "m/s",
    "WRADH": "m/s",
    "WIDTH": "m/s",
    "spectrum_width": "m/s",
    "ZDR": "dB",
    "differential_reflectivity": "dB",
    "RHOHV": "",
    "cross_correlation_ratio": "",
    "PHIDP": "deg",
    "differential_phase": "deg",
    "KDP": "deg/km",
    "specific_differential_phase": "deg/km",
}


def _get_cmap(variable: str) -> str:
    """Return a matplotlib colormap name for the variable."""
    cmap_name = _VARIABLE_CMAPS.get(variable)
    if cmap_name and cmap_name in plt.colormaps():
        return cmap_name
    # Fallback colormaps
    vlow = variable.lower()
    if any(tok in vlow for tok in ("dbz", "reflectivity", "power")):
        return "jet"
    if any(tok in vlow for tok in ("vel", "vrad")):
        return "RdBu_r"
    if any(tok in vlow for tok in ("width", "wrad", "spw")):
        return "hot"
    return "viridis"


def _get_range(variable: str) -> tuple[float | None, float | None]:
    r = _VARIABLE_RANGES.get(variable)
    return r if r is not None else (None, None)


def _get_units(variable: str, data_attrs: dict[str, Any] | None = None) -> str:
    """Resolve units from data attributes or fallback table."""
    if data_attrs:
        u = data_attrs.get("units", data_attrs.get("unit", ""))
        if u:
            return str(u)
    return _VARIABLE_UNITS.get(variable, "")


def _extract_sweep_dataset(
    datatree: xr.DataTree,
    sweep: int,
) -> xr.Dataset:
    """Extract the dataset for a given sweep index."""
    sweep_nodes = sorted([n for n in datatree.children if n.startswith("sweep_")])
    if sweep < 0 or sweep >= len(sweep_nodes):
        raise IndexError(f"Sweep {sweep} out of range (0..{len(sweep_nodes) - 1})")
    return datatree[sweep_nodes[sweep]].to_dataset()


def _extract_datetime(datatree: xr.DataTree, sweep_ds: xr.Dataset) -> str:
    """Best-effort datetime extraction for titles."""
    # Try time coordinate in sweep
    if "time" in sweep_ds.coords:
        t = sweep_ds.coords["time"].values
        if hasattr(t, "__len__") and len(t) > 0:
            t0 = np.datetime_as_string(t[0], unit="s")
            return str(t0).replace("T", " ")
    # Try root attributes
    try:
        root_ds = datatree.to_dataset()
        for key in ("time_coverage_start", "start_datetime", "time"):
            if key in root_ds.attrs:
                return str(root_ds.attrs[key])
    except Exception:
        pass
    return ""


def _extract_elevation(sweep_ds: xr.Dataset) -> str:
    """Best-effort elevation extraction."""
    if "sweep_fixed_angle" in sweep_ds.attrs:
        return f"{float(sweep_ds.attrs['sweep_fixed_angle']):.1f}"
    if "elevation" in sweep_ds.coords:
        elev = sweep_ds.coords["elevation"].values
        if hasattr(elev, "__len__") and len(elev) > 0:
            return f"{float(np.nanmean(elev)):.1f}"
    return "?"


def _count_sweep_nodes(datatree: xr.DataTree) -> int:
    return len([n for n in datatree.children if n.startswith("sweep_")])


# ---------------------------------------------------------------------------
# Publication-quality figure rendering
# ---------------------------------------------------------------------------


def render_publication_figure(
    datatree: xr.DataTree,
    variable: str,
    sweep: int,
    *,
    dpi: int = DEFAULT_DPI,
    fig_size: float = DEFAULT_FIG_SIZE,
    font_size: int = DEFAULT_FONT_SIZE,
    show_rings: bool = True,
    show_colorbar: bool = True,
    show_title: bool = True,
    title_override: str | None = None,
    dark_background: bool = False,
) -> plt.Figure:
    """Create a publication-quality matplotlib figure of a PPI sweep.

    Returns the Figure object (caller is responsible for saving/closing).
    """
    sweep_ds = _extract_sweep_dataset(datatree, sweep)

    if variable not in sweep_ds.data_vars:
        available = list(sweep_ds.data_vars)
        if not available:
            raise ValueError(f"No data variables in sweep {sweep}")
        raise KeyError(f"Variable '{variable}' not found in sweep {sweep}. Available: {available}")

    da = sweep_ds[variable]
    values = da.values.astype(np.float64)

    # Polar coordinates
    if "azimuth" in da.coords:
        azimuth = da.coords["azimuth"].values.astype(np.float64)
    else:
        azimuth = np.arange(values.shape[0], dtype=np.float64)

    if "range" in da.coords:
        range_m = da.coords["range"].values.astype(np.float64)
    else:
        range_m = np.arange(values.shape[-1], dtype=np.float64)

    range_km = range_m / 1000.0
    az_rad = np.deg2rad(azimuth)

    # Create meshgrid for pcolormesh
    # Add an extra azimuth bin for wrapping
    az_edges = np.concatenate([az_rad, [az_rad[0] + 2 * np.pi]])
    # Range edges: midpoints between bins, extended at start/end
    if len(range_km) > 1:
        dr = np.diff(range_km)
        range_edges = np.concatenate(
            [
                [range_km[0] - dr[0] / 2],
                range_km[:-1] + dr / 2,
                [range_km[-1] + dr[-1] / 2],
            ]
        )
    else:
        range_edges = np.array([0, range_km[0] * 2])

    # Cartesian transform
    r_grid, az_grid = np.meshgrid(range_edges, az_edges)
    x = r_grid * np.sin(az_grid)
    y = r_grid * np.cos(az_grid)

    # Color range
    vmin, vmax = _get_range(variable)
    if vmin is None:
        vmin = float(np.nanmin(values))
    if vmax is None:
        vmax = float(np.nanmax(values))

    units = _get_units(variable, dict(da.attrs))
    cmap_name = _get_cmap(variable)

    # Style
    if dark_background:
        plt.style.use("dark_background")
    else:
        plt.rcdefaults()

    plt.rcParams.update(
        {
            "font.size": font_size,
            "axes.labelsize": font_size,
            "axes.titlesize": font_size + 2,
            "xtick.labelsize": font_size - 2,
            "ytick.labelsize": font_size - 2,
            "figure.titlesize": font_size + 4,
        }
    )

    fig, ax = plt.subplots(
        figsize=(fig_size, fig_size),
        dpi=dpi,
    )

    # Plot PPI
    mesh = ax.pcolormesh(
        x,
        y,
        values,
        cmap=cmap_name,
        vmin=vmin,
        vmax=vmax,
        shading="flat",
    )

    # Equal aspect ratio
    ax.set_aspect("equal")
    max_r = float(range_km.max())
    ax.set_xlim(-max_r, max_r)
    ax.set_ylim(-max_r, max_r)

    # Axis labels
    ax.set_xlabel("East-West Distance (km)")
    ax.set_ylabel("North-South Distance (km)")

    # Range rings
    if show_rings:
        ring_interval = _pick_ring_interval_km(max_r)
        for r in np.arange(ring_interval, max_r + ring_interval, ring_interval):
            if r > max_r * 1.01:
                break
            theta = np.linspace(0, 2 * np.pi, 360)
            ax.plot(
                r * np.cos(theta),
                r * np.sin(theta),
                color="gray",
                linewidth=0.5,
                linestyle="--",
                alpha=0.5,
            )
            # Label ring at 45-degree position
            ax.text(
                r * np.cos(np.pi / 4) + max_r * 0.01,
                r * np.sin(np.pi / 4) + max_r * 0.01,
                f"{r:.0f} km",
                fontsize=font_size - 3,
                color="gray",
                alpha=0.7,
            )

        # Crosshair lines
        ax.axhline(0, color="gray", linewidth=0.3, alpha=0.4)
        ax.axvline(0, color="gray", linewidth=0.3, alpha=0.4)

        # Cardinal direction labels
        offset = max_r * 1.05
        ax.text(
            0,
            offset,
            "N",
            ha="center",
            va="bottom",
            fontsize=font_size - 1,
            fontweight="bold",
            color="gray",
        )
        ax.text(
            0,
            -offset,
            "S",
            ha="center",
            va="top",
            fontsize=font_size - 1,
            fontweight="bold",
            color="gray",
        )
        ax.text(
            offset,
            0,
            "E",
            ha="left",
            va="center",
            fontsize=font_size - 1,
            fontweight="bold",
            color="gray",
        )
        ax.text(
            -offset,
            0,
            "W",
            ha="right",
            va="center",
            fontsize=font_size - 1,
            fontweight="bold",
            color="gray",
        )

    # Colorbar
    if show_colorbar:
        cbar = fig.colorbar(mesh, ax=ax, pad=0.02, fraction=0.046)
        label = f"{variable}"
        if units:
            label += f" ({units})"
        cbar.set_label(label)

    # Title
    if show_title:
        if title_override:
            title = title_override
        else:
            elev = _extract_elevation(sweep_ds)
            dt_str = _extract_datetime(datatree, sweep_ds)
            parts = [variable]
            if elev != "?":
                parts.append(f"Elev: {elev} deg")
            parts.append(f"Sweep {sweep}")
            if dt_str:
                parts.append(dt_str)
            title = " | ".join(parts)
        ax.set_title(title, pad=12)

    fig.tight_layout()
    return fig


def _pick_ring_interval_km(max_range_km: float) -> float:
    """Pick ring interval that gives 4-6 rings."""
    candidates = [5, 10, 20, 25, 50, 100, 150, 200, 250, 500]
    for spacing in candidates:
        n = int(max_range_km / spacing)
        if 3 <= n <= 6:
            return float(spacing)
    return max_range_km / 5.0


# ---------------------------------------------------------------------------
# Main exporter class
# ---------------------------------------------------------------------------


class RadarExporter:
    """Export radar data to image, data, and animation formats.

    Image formats: PNG, SVG, PDF via matplotlib savefig.
    Data formats: NetCDF (CfRadial1), CfRadial2, CSV, Zarr.
    Animation: GIF via Pillow.
    """

    def export(
        self,
        datatree: xr.DataTree,
        fmt: str,
        dpi: int = DEFAULT_DPI,
        output_path: str | None = None,
        variable: str | None = None,
        sweep: int = 0,
        progress: ProgressCallback = _noop_progress,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Export a single sweep/variable to the specified format."""
        if not isinstance(fmt, str) or not fmt.strip():
            raise ValueError("Export format must be a non-empty string")
        if not isinstance(dpi, int) or dpi < MIN_DPI or dpi > MAX_DPI:
            raise ValueError(f"DPI must be an integer between {MIN_DPI} and {MAX_DPI}, got {dpi}")
        if not isinstance(sweep, int) or sweep < 0:
            raise ValueError(f"Sweep must be a non-negative integer, got {sweep}")

        fmt = fmt.lower().strip()
        if options is None:
            options = {}

        # Resolve variable if not specified
        if variable is None:
            sweep_ds = _extract_sweep_dataset(datatree, sweep)
            data_vars = list(sweep_ds.data_vars)
            if not data_vars:
                raise ValueError(f"No data variables in sweep {sweep}")
            variable = str(data_vars[0])

        dispatch: dict[str, Callable[..., str]] = {
            "png": self._export_figure,
            "svg": self._export_figure,
            "pdf": self._export_figure,
            "netcdf": self._export_netcdf,
            "cfradial2": self._export_cfradial2,
            "csv": self._export_csv,
            "zarr": self._export_zarr,
            "geotiff": self._export_geotiff,
        }
        handler = dispatch.get(fmt)
        if handler is None:
            raise ValueError(
                f"Unsupported export format '{fmt}'. Supported: {sorted(dispatch.keys())}"
            )
        return handler(
            datatree,
            fmt=fmt,
            dpi=dpi,
            output_path=output_path,
            variable=variable,
            sweep=sweep,
            progress=progress,
            options=options,
        )

    # ------------------------------------------------------------------
    # Image export via matplotlib
    # ------------------------------------------------------------------

    def _export_figure(
        self,
        datatree: xr.DataTree,
        *,
        fmt: str,
        dpi: int,
        output_path: str | None,
        variable: str,
        sweep: int,
        progress: ProgressCallback,
        options: dict[str, Any],
    ) -> str:
        progress(10, f"Preparing publication-quality {fmt.upper()} export")

        fig_size = options.get("fig_size", DEFAULT_FIG_SIZE)
        font_size = options.get("font_size", DEFAULT_FONT_SIZE)
        show_rings = options.get("show_rings", True)
        show_colorbar = options.get("show_colorbar", True)
        show_title = options.get("show_title", True)
        title_override = options.get("title")
        dark_bg = options.get("dark_background", False)

        progress(30, "Rendering figure")

        fig = render_publication_figure(
            datatree,
            variable,
            sweep,
            dpi=dpi,
            fig_size=fig_size,
            font_size=font_size,
            show_rings=show_rings,
            show_colorbar=show_colorbar,
            show_title=show_title,
            title_override=title_override,
            dark_background=dark_bg,
        )

        progress(70, "Saving file")

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=f".{fmt}", prefix="xradar_export_")
            os.close(fd)

        fig.savefig(
            output_path,
            format=fmt,
            dpi=dpi,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
            edgecolor="none",
        )
        plt.close(fig)

        output_path = str(Path(output_path).resolve())
        progress(100, f"Export complete: {output_path}")
        logger.info("Exported %s to %s", fmt, output_path)
        return output_path

    # ------------------------------------------------------------------
    # Batch export
    # ------------------------------------------------------------------

    def batch_export(
        self,
        datatree: xr.DataTree,
        *,
        variables: list[str],
        sweeps: list[int],
        fmt: str = "png",
        dpi: int = DEFAULT_DPI,
        output_dir: str | None = None,
        progress: ProgressCallback = _noop_progress,
        options: dict[str, Any] | None = None,
    ) -> list[str]:
        """Batch export multiple variables/sweeps.

        Returns list of saved file paths.
        """
        if options is None:
            options = {}

        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="xradar_batch_")
        else:
            os.makedirs(output_dir, exist_ok=True)

        total = len(variables) * len(sweeps)
        if total == 0:
            raise ValueError("No variables or sweeps selected for batch export")

        saved: list[str] = []
        count = 0

        for var in variables:
            for sw in sweeps:
                count += 1
                pct = int(count / total * 90) + 5  # 5-95%
                progress(pct, f"Exporting {var} sweep {sw} ({count}/{total})")

                filename = f"{var}_sweep{sw:02d}.{fmt}"
                filepath = str(Path(output_dir) / filename)

                try:
                    path = self.export(
                        datatree,
                        fmt=fmt,
                        dpi=dpi,
                        output_path=filepath,
                        variable=var,
                        sweep=sw,
                        progress=_noop_progress,
                        options=options,
                    )
                    saved.append(path)
                except Exception as exc:
                    logger.warning("Batch export failed for %s sweep %d: %s", var, sw, exc)
                    # Continue with remaining exports

        progress(100, f"Batch export complete: {len(saved)}/{total} files")
        return saved

    # ------------------------------------------------------------------
    # Animation export (GIF)
    # ------------------------------------------------------------------

    def export_animation(
        self,
        datatree: xr.DataTree,
        *,
        variable: str,
        sweeps: list[int],
        output_path: str | None = None,
        frame_duration_ms: int = 500,
        dpi: int = 150,
        fig_size: float = 8.0,
        progress: ProgressCallback = _noop_progress,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Export an animated GIF cycling through sweeps for a given variable."""
        from PIL import Image as PILImage

        if options is None:
            options = {}
        if not sweeps:
            raise ValueError("No sweeps specified for animation")

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".gif", prefix="xradar_anim_")
            os.close(fd)

        frames: list[PILImage.Image] = []
        total = len(sweeps)

        for i, sw in enumerate(sweeps):
            pct = int((i + 1) / total * 85) + 5
            progress(pct, f"Rendering frame {i + 1}/{total} (sweep {sw})")

            fig = render_publication_figure(
                datatree,
                variable,
                sw,
                dpi=dpi,
                fig_size=fig_size,
                font_size=options.get("font_size", DEFAULT_FONT_SIZE),
                show_rings=options.get("show_rings", True),
                show_colorbar=True,
                show_title=True,
                dark_background=options.get("dark_background", False),
            )

            # Render figure to PIL Image
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            frame = PILImage.open(buf).convert("RGBA")
            frames.append(frame)

        progress(90, "Assembling GIF")

        # Convert RGBA to RGB with white background for GIF
        rgb_frames: list[PILImage.Image] = []
        for frame in frames:
            bg = PILImage.new("RGB", frame.size, (255, 255, 255))
            bg.paste(frame, mask=frame.split()[3])
            rgb_frames.append(bg)

        rgb_frames[0].save(
            output_path,
            save_all=True,
            append_images=rgb_frames[1:],
            duration=frame_duration_ms,
            loop=0,
            optimize=True,
        )

        output_path = str(Path(output_path).resolve())
        progress(100, f"Animation export complete: {output_path}")
        logger.info("Exported GIF animation to %s (%d frames)", output_path, len(frames))
        return output_path

    # ------------------------------------------------------------------
    # Data format exports
    # ------------------------------------------------------------------

    def _export_netcdf(
        self,
        datatree: xr.DataTree,
        *,
        fmt: str,
        dpi: int,
        output_path: str | None,
        variable: str,
        sweep: int,
        progress: ProgressCallback,
        options: dict[str, Any],
    ) -> str:
        from xradar.io.export.cfradial1 import to_cfradial1

        progress(10, "Preparing CfRadial1 NetCDF export")
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".nc", prefix="xradar_export_")
            os.close(fd)

        to_cfradial1(dtree=datatree, filename=output_path)

        progress(100, f"CfRadial1 export complete: {output_path}")
        return str(Path(output_path).resolve())

    def _export_cfradial2(
        self,
        datatree: xr.DataTree,
        *,
        fmt: str,
        dpi: int,
        output_path: str | None,
        variable: str,
        sweep: int,
        progress: ProgressCallback,
        options: dict[str, Any],
    ) -> str:
        from xradar.io.export.cfradial2 import to_cfradial2

        progress(10, "Preparing CfRadial2 export")
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".nc", prefix="xradar_cfradial2_")
            os.close(fd)

        to_cfradial2(datatree, output_path)

        progress(100, f"CfRadial2 export complete: {output_path}")
        return str(Path(output_path).resolve())

    def _export_csv(
        self,
        datatree: xr.DataTree,
        *,
        fmt: str,
        dpi: int,
        output_path: str | None,
        variable: str,
        sweep: int,
        progress: ProgressCallback,
        options: dict[str, Any],
    ) -> str:
        """Export sweep data as a CSV table: azimuth, range, value."""
        import pandas as pd

        progress(10, "Preparing CSV export")

        sweep_ds = _extract_sweep_dataset(datatree, sweep)
        if variable not in sweep_ds.data_vars:
            raise KeyError(f"Variable '{variable}' not found in sweep {sweep}")

        da = sweep_ds[variable]
        values = da.values

        azimuth = (
            da.coords["azimuth"].values.astype(float)
            if "azimuth" in da.coords
            else np.arange(values.shape[0], dtype=float)
        )
        range_m = (
            da.coords["range"].values.astype(float)
            if "range" in da.coords
            else np.arange(values.shape[-1], dtype=float)
        )

        progress(40, "Building data table")

        az_2d, rng_2d = np.meshgrid(azimuth, range_m, indexing="ij")
        mask = np.isfinite(values)

        df = pd.DataFrame(
            {
                "azimuth_deg": az_2d[mask].ravel(),
                "range_m": rng_2d[mask].ravel(),
                variable: values[mask].ravel(),
            }
        )

        progress(70, "Writing CSV")

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".csv", prefix="xradar_export_")
            os.close(fd)

        df.to_csv(output_path, index=False, float_format="%.4f")

        output_path = str(Path(output_path).resolve())
        progress(100, f"CSV export complete: {output_path}")
        logger.info("Exported CSV to %s (%d rows)", output_path, len(df))
        return output_path

    def _export_geotiff(
        self,
        datatree: xr.DataTree,
        **kwargs: Any,
    ) -> str:
        raise NotImplementedError("GeoTIFF export is planned for a future release")

    def _export_zarr(
        self,
        datatree: xr.DataTree,
        *,
        fmt: str,
        dpi: int,
        output_path: str | None,
        variable: str,
        sweep: int,
        progress: ProgressCallback,
        options: dict[str, Any],
    ) -> str:
        progress(10, "Preparing Zarr export")
        if output_path is None:
            output_path = tempfile.mkdtemp(prefix="xradar_export_", suffix=".zarr")

        datatree.to_zarr(output_path, mode="w")

        progress(100, f"Zarr export complete: {output_path}")
        return str(Path(output_path).resolve())
