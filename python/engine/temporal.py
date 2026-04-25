"""Temporal analysis tools for multi-file radar comparisons.

Provides time series extraction, element-wise differencing,
accumulation, and linear trend computation across multiple
radar files.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)


def compute_time_series(
    readers: dict[str, Any],
    variable: str,
    sweep: int,
    point_az: float,
    point_range: float,
) -> dict[str, Any]:
    """Extract the value at a fixed (azimuth, range) point across multiple files.

    Parameters
    ----------
    readers : dict[str, RadarReader]
        Mapping of file_id -> RadarReader instances.
    variable : str
        Variable name (e.g. ``"DBZH"``).
    sweep : int
        Sweep index to query.
    point_az : float
        Azimuth angle in degrees.
    point_range : float
        Range distance in metres.

    Returns
    -------
    dict
        ``{file_ids: [...], values: [...], timestamps: [...], variable, sweep,
          azimuth, range_m}``
    """
    file_ids: list[str] = []
    values: list[float | None] = []
    timestamps: list[str | None] = []

    for fid, reader in readers.items():
        try:
            da = reader.get_sweep_data(sweep, variable)
            val = _sample_point(da, point_az, point_range)
            ts = _extract_timestamp(reader)
            file_ids.append(fid)
            values.append(val)
            timestamps.append(ts)
        except Exception as exc:
            logger.debug("Skipping file %s in time series: %s", fid, exc)
            file_ids.append(fid)
            values.append(None)
            timestamps.append(None)

    return {
        "file_ids": file_ids,
        "values": values,
        "timestamps": timestamps,
        "variable": variable,
        "sweep": sweep,
        "azimuth": point_az,
        "range_m": point_range,
    }


def compute_difference(
    reader1: Any,
    reader2: Any,
    variable: str,
    sweep: int,
) -> dict[str, Any]:
    """Compute element-wise difference (reader2 - reader1) for a sweep.

    Parameters
    ----------
    reader1, reader2 : RadarReader
        Two open RadarReader instances.
    variable : str
        Radar moment variable name.
    sweep : int
        Sweep index.

    Returns
    -------
    dict
        ``{diff: list[list[float]], n_az: int, n_range: int, vmin: float,
          vmax: float, variable, sweep}``
    """
    da1 = reader1.get_sweep_data(sweep, variable)
    da2 = reader2.get_sweep_data(sweep, variable)

    v1 = da1.values.astype(np.float64)
    v2 = da2.values.astype(np.float64)

    # Handle shape mismatch by trimming to smallest common shape
    min_az = min(v1.shape[0], v2.shape[0])
    min_rng = min(v1.shape[1], v2.shape[1])
    v1 = v1[:min_az, :min_rng]
    v2 = v2[:min_az, :min_rng]

    diff = v2 - v1

    # Replace NaN-contaminated cells
    diff = np.where(np.isfinite(diff), diff, np.nan)

    valid = diff[np.isfinite(diff)]
    vmin = float(np.nanmin(valid)) if len(valid) > 0 else -1.0
    vmax = float(np.nanmax(valid)) if len(valid) > 0 else 1.0

    # Make symmetric about zero for diverging colormaps
    abs_max = max(abs(vmin), abs(vmax))

    # Get coordinate arrays for the client
    az1 = (
        da1.coords["azimuth"].values.astype(np.float32)
        if "azimuth" in da1.coords
        else np.arange(min_az, dtype=np.float32)
    )
    rng1 = (
        da1.coords["range"].values.astype(np.float32)
        if "range" in da1.coords
        else np.arange(min_rng, dtype=np.float32)
    )
    az1 = az1[:min_az]
    rng1 = rng1[:min_rng]

    diff_f32 = np.nan_to_num(diff.astype(np.float32), nan=-9999.0)

    return {
        "diff_values": diff_f32.tobytes(),
        "azimuth": az1.tobytes(),
        "range": rng1.tobytes(),
        "n_az": min_az,
        "n_range": min_rng,
        "vmin": -abs_max,
        "vmax": abs_max,
        "max_range": float(rng1.max()) if len(rng1) > 0 else 1.0,
        "variable": variable,
        "sweep": sweep,
        "azimuth_bytes": len(az1.tobytes()),
        "range_bytes": len(rng1.tobytes()),
        "diff_bytes": len(diff_f32.tobytes()),
    }


def compute_accumulation(
    readers: dict[str, Any],
    variable: str,
    sweep: int,
    method: Literal["sum", "mean", "max"] = "sum",
) -> dict[str, Any]:
    """Accumulate a variable across multiple files.

    Useful for rainfall accumulation (sum), average reflectivity (mean),
    or max composite (max).

    Parameters
    ----------
    readers : dict[str, RadarReader]
    variable : str
    sweep : int
    method : {"sum", "mean", "max"}

    Returns
    -------
    dict
        ``{values: list[list[float]], n_az, n_range, vmin, vmax, n_files,
          method, variable, sweep}``
    """
    arrays: list[np.ndarray] = []
    ref_da = None

    for fid, reader in readers.items():
        try:
            da = reader.get_sweep_data(sweep, variable)
            if ref_da is None:
                ref_da = da
            arrays.append(da.values.astype(np.float64))
        except Exception as exc:
            logger.debug("Skipping file %s in accumulation: %s", fid, exc)

    if not arrays or ref_da is None:
        raise ValueError("No valid data found for accumulation")

    # Align to smallest common shape
    min_az = min(a.shape[0] for a in arrays)
    min_rng = min(a.shape[1] for a in arrays)
    aligned = [a[:min_az, :min_rng] for a in arrays]

    stack = np.stack(aligned, axis=0)

    if method == "sum":
        result = np.nansum(stack, axis=0)
    elif method == "mean":
        result = np.nanmean(stack, axis=0)
    elif method == "max":
        result = np.nanmax(stack, axis=0)
    else:
        raise ValueError(f"Unknown accumulation method: {method}")

    valid = result[np.isfinite(result)]
    vmin = float(np.nanmin(valid)) if len(valid) > 0 else 0.0
    vmax = float(np.nanmax(valid)) if len(valid) > 0 else 1.0

    return {
        "values": result.tolist(),
        "n_az": min_az,
        "n_range": min_rng,
        "vmin": vmin,
        "vmax": vmax,
        "n_files": len(arrays),
        "method": method,
        "variable": variable,
        "sweep": sweep,
    }


def compute_trend(
    readers: dict[str, Any],
    variable: str,
    sweep: int,
    point_az: float,
    point_range: float,
) -> dict[str, Any]:
    """Compute a linear trend (slope) at a fixed point across files.

    Parameters
    ----------
    readers : dict[str, RadarReader]
    variable, sweep, point_az, point_range : as in compute_time_series

    Returns
    -------
    dict
        ``{slope, intercept, r_squared, n_points, values, file_ids}``
    """
    ts = compute_time_series(readers, variable, sweep, point_az, point_range)

    valid_idx = [i for i, v in enumerate(ts["values"]) if v is not None and np.isfinite(v)]

    if len(valid_idx) < 2:
        return {
            "slope": None,
            "intercept": None,
            "r_squared": None,
            "n_points": len(valid_idx),
            "values": ts["values"],
            "file_ids": ts["file_ids"],
            "timestamps": ts["timestamps"],
            "variable": variable,
            "sweep": sweep,
            "azimuth": point_az,
            "range_m": point_range,
        }

    x = np.array(valid_idx, dtype=np.float64)
    y = np.array([ts["values"][i] for i in valid_idx], dtype=np.float64)

    # Linear regression: y = slope * x + intercept
    n = len(x)
    sx = np.sum(x)
    sy = np.sum(y)
    sxx = np.sum(x * x)
    sxy = np.sum(x * y)

    denom = n * sxx - sx * sx
    if abs(denom) < 1e-15:
        slope = 0.0
        intercept = float(np.mean(y))
        r_squared = 0.0
    else:
        slope = float((n * sxy - sx * sy) / denom)
        intercept = float((sy - slope * sx) / n)

        # R-squared
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_squared,
        "n_points": n,
        "values": ts["values"],
        "file_ids": ts["file_ids"],
        "timestamps": ts["timestamps"],
        "variable": variable,
        "sweep": sweep,
        "azimuth": point_az,
        "range_m": point_range,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────


def _sample_point(da: xr.DataArray, az_deg: float, range_m: float) -> float | None:
    """Sample the nearest-neighbour value from a DataArray at (azimuth, range)."""
    if "azimuth" not in da.coords or "range" not in da.coords:
        return None

    az = da.coords["azimuth"].values
    rng = da.coords["range"].values

    az_idx = int(np.argmin(np.abs(az - az_deg)))
    rng_idx = int(np.argmin(np.abs(rng - range_m)))

    val = float(da.values[az_idx, rng_idx])
    return val if np.isfinite(val) else None


def _extract_timestamp(reader: Any) -> str | None:
    """Try to pull a timestamp string from a reader's datatree attributes."""
    dtree = reader.datatree
    if dtree is None:
        return None

    try:
        root_ds = dtree.to_dataset()
        attrs = root_ds.attrs

        # Common attribute names for radar file timestamps
        for key in (
            "time_coverage_start",
            "time",
            "date",
            "timestamp",
            "scan_time",
            "start_datetime",
            "start_time",
        ):
            if key in attrs:
                return str(attrs[key])

        # Check for a time coordinate in the first sweep
        for name in sorted(dtree.children):
            if name.startswith("sweep_"):
                ds = dtree[name].to_dataset()
                if "time" in ds.coords:
                    t = ds.coords["time"].values
                    if hasattr(t, "__len__") and len(t) > 0:
                        return str(t[0])
                    return str(t)
                break
    except Exception:
        pass

    return None
