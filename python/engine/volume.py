"""3D volume grid extraction and cross-section from radar DataTree.

Converts polar radar sweeps into regular Cartesian grids using scattered
interpolation, suitable for 3D visualisation in the frontend.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

NODATA: float = -9999.0


def _polar_to_cartesian(
    azimuth: np.ndarray,
    range_m: np.ndarray,
    elevation: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert polar coordinates to Cartesian (x, y, z) in metres.

    Parameters
    ----------
    azimuth : 1-D array of azimuth angles in degrees (length n_az)
    range_m : 1-D array of range gates in metres (length n_range)
    elevation : scalar elevation angle in degrees

    Returns
    -------
    x, y, z : 2-D arrays of shape (n_az, n_range)
    """
    az_rad = np.deg2rad(azimuth)
    el_rad = np.deg2rad(elevation)

    # Meshgrid: rows = azimuth, cols = range
    az_2d, r_2d = np.meshgrid(az_rad, range_m, indexing="ij")

    cos_el = np.cos(el_rad)
    sin_el = np.sin(el_rad)

    x = r_2d * cos_el * np.sin(az_2d)
    y = r_2d * cos_el * np.cos(az_2d)
    z = r_2d * sin_el

    return x, y, z


def _get_sweep_groups(dt: xr.DataTree) -> list[str]:
    """Return sorted sweep group names from a DataTree."""
    groups = []
    for name in dt.children:
        if name.startswith("sweep_"):
            groups.append(name)
    groups.sort(key=lambda s: int(s.split("_")[1]))
    return groups


def extract_volume(
    dt: xr.DataTree,
    variable: str,
    box: dict[str, float],
    resolution: int = 100,
    z_max: float = 15000.0,
) -> dict[str, Any]:
    """Extract a 3D Cartesian volume grid from all sweeps of a DataTree.

    Parameters
    ----------
    dt : xr.DataTree
        Radar DataTree opened by xradar.
    variable : str
        Name of the data variable (e.g. ``"DBZH"``).
    box : dict
        Bounding box in metres from radar:
        ``{x_min, x_max, y_min, y_max}``.
    resolution : int
        Number of grid points per spatial axis (default 100).
    z_max : float
        Maximum height in metres (default 15 000).

    Returns
    -------
    dict with keys:
        data : np.ndarray — flat float32 array (x-major order)
        nx, ny, nz : int — grid dimensions
        x_min, x_max, y_min, y_max, z_min, z_max : float — bounds
        vmin, vmax : float — data value range
    """
    from scipy.interpolate import LinearNDInterpolator

    x_min = float(box.get("x_min", -50000))
    x_max = float(box.get("x_max", 50000))
    y_min = float(box.get("y_min", -50000))
    y_max = float(box.get("y_max", 50000))
    z_min = 0.0
    z_max = float(z_max)

    nx = ny = nz = int(resolution)

    sweep_groups = _get_sweep_groups(dt)
    if not sweep_groups:
        raise ValueError("No sweep groups found in DataTree")

    # Collect scattered points from all sweeps
    all_x: list[np.ndarray] = []
    all_y: list[np.ndarray] = []
    all_z: list[np.ndarray] = []
    all_v: list[np.ndarray] = []

    for grp_name in sweep_groups:
        try:
            ds = dt[grp_name].to_dataset()
        except (KeyError, ValueError):
            continue

        if variable not in ds.data_vars:
            continue

        da = ds[variable]

        # Elevation — scalar or per-ray
        if "elevation" in ds.coords:
            elev_vals = ds.coords["elevation"].values
            elev_mean = float(np.nanmean(elev_vals))
        elif "elevation" in ds.data_vars:
            elev_mean = float(np.nanmean(ds["elevation"].values))
        else:
            logger.debug("Skipping %s — no elevation coordinate", grp_name)
            continue

        # Azimuth and range
        if "azimuth" in ds.coords:
            azimuth = ds.coords["azimuth"].values.astype(np.float64)
        elif "azimuth" in ds.data_vars:
            azimuth = ds["azimuth"].values.astype(np.float64)
        else:
            continue

        if "range" in ds.coords:
            range_m = ds.coords["range"].values.astype(np.float64)
        elif "range" in ds.data_vars:
            range_m = ds["range"].values.astype(np.float64)
        else:
            continue

        values = da.values.astype(np.float64)
        if values.ndim != 2:
            continue

        x, y, z = _polar_to_cartesian(azimuth, range_m, elev_mean)

        # Mask: inside bounding box AND finite values
        mask = (
            (x >= x_min) & (x <= x_max)
            & (y >= y_min) & (y <= y_max)
            & (z >= z_min) & (z <= z_max)
            & np.isfinite(values)
        )

        if not np.any(mask):
            continue

        all_x.append(x[mask])
        all_y.append(y[mask])
        all_z.append(z[mask])
        all_v.append(values[mask])

    if not all_x:
        # No valid data — return an empty grid
        empty = np.full(nx * ny * nz, NODATA, dtype=np.float32)
        return {
            "data": empty,
            "nx": nx, "ny": ny, "nz": nz,
            "x_min": x_min, "x_max": x_max,
            "y_min": y_min, "y_max": y_max,
            "z_min": z_min, "z_max": z_max,
            "vmin": 0.0, "vmax": 0.0,
        }

    pts_x = np.concatenate(all_x)
    pts_y = np.concatenate(all_y)
    pts_z = np.concatenate(all_z)
    pts_v = np.concatenate(all_v)

    logger.info(
        "Volume interpolation: %d scattered points -> (%d, %d, %d) grid",
        len(pts_v), nx, ny, nz,
    )

    # Build the regular grid
    gx = np.linspace(x_min, x_max, nx)
    gy = np.linspace(y_min, y_max, ny)
    gz = np.linspace(z_min, z_max, nz)

    # 3D meshgrid — output shape (nx, ny, nz)
    gx3, gy3, gz3 = np.meshgrid(gx, gy, gz, indexing="ij")

    # Interpolate
    points = np.column_stack((pts_x, pts_y, pts_z))
    interp = LinearNDInterpolator(points, pts_v, fill_value=np.nan)
    grid = interp(gx3, gy3, gz3)  # shape (nx, ny, nz)

    # Compute data range before filling NaN
    valid = grid[np.isfinite(grid)]
    if len(valid) > 0:
        vmin = float(np.nanmin(valid))
        vmax = float(np.nanmax(valid))
    else:
        vmin = 0.0
        vmax = 0.0

    # Replace NaN with NODATA sentinel
    grid = np.nan_to_num(grid, nan=NODATA).astype(np.float32)

    # Flatten in x-major (C) order — already the default for row-major
    flat = grid.ravel(order="C")

    return {
        "data": flat,
        "nx": nx, "ny": ny, "nz": nz,
        "x_min": x_min, "x_max": x_max,
        "y_min": y_min, "y_max": y_max,
        "z_min": z_min, "z_max": z_max,
        "vmin": vmin, "vmax": vmax,
    }


def extract_cross_section_3d(
    dt: xr.DataTree,
    variable: str,
    start: dict[str, float],
    end: dict[str, float],
    width: float = 2000.0,
    n_horizontal: int = 200,
    n_vertical: int = 100,
    z_max: float = 15000.0,
) -> dict[str, Any]:
    """Extract a vertical curtain (cross-section) along a line in Cartesian space.

    Parameters
    ----------
    dt : xr.DataTree
        Radar DataTree.
    variable : str
        Data variable name.
    start, end : dict
        ``{x, y}`` coordinates in metres from radar.
    width : float
        Perpendicular averaging width in metres (default 2000).
    n_horizontal : int
        Number of grid points along the horizontal line (default 200).
    n_vertical : int
        Number of grid points in the vertical (default 100).
    z_max : float
        Maximum height in metres (default 15 000).

    Returns
    -------
    dict with keys:
        data : np.ndarray — flat float32 array (distance-major)
        n_dist, n_z : int — 2D grid dimensions
        dist_min, dist_max, z_min, z_max : float
        vmin, vmax : float
    """
    from scipy.interpolate import LinearNDInterpolator

    sx, sy = float(start["x"]), float(start["y"])
    ex, ey = float(end["x"]), float(end["y"])

    dx = ex - sx
    dy = ey - sy
    line_len = np.sqrt(dx * dx + dy * dy)
    if line_len < 1.0:
        raise ValueError("Start and end points are too close together")

    # Unit vectors: along line and perpendicular
    ux, uy = dx / line_len, dy / line_len
    px, py = -uy, ux  # perpendicular

    half_w = width / 2.0
    z_min_val = 0.0

    # Expand the bounding box to include the corridor
    corners_x = [
        sx - px * half_w, sx + px * half_w,
        ex - px * half_w, ex + px * half_w,
    ]
    corners_y = [
        sy - py * half_w, sy + py * half_w,
        ey - py * half_w, ey + py * half_w,
    ]
    box_x_min = min(corners_x)
    box_x_max = max(corners_x)
    box_y_min = min(corners_y)
    box_y_max = max(corners_y)

    sweep_groups = _get_sweep_groups(dt)
    if not sweep_groups:
        raise ValueError("No sweep groups found in DataTree")

    all_d: list[np.ndarray] = []  # distance along line
    all_z: list[np.ndarray] = []
    all_v: list[np.ndarray] = []

    for grp_name in sweep_groups:
        try:
            ds = dt[grp_name].to_dataset()
        except (KeyError, ValueError):
            continue

        if variable not in ds.data_vars:
            continue

        da = ds[variable]

        if "elevation" in ds.coords:
            elev_mean = float(np.nanmean(ds.coords["elevation"].values))
        elif "elevation" in ds.data_vars:
            elev_mean = float(np.nanmean(ds["elevation"].values))
        else:
            continue

        if "azimuth" in ds.coords:
            azimuth = ds.coords["azimuth"].values.astype(np.float64)
        elif "azimuth" in ds.data_vars:
            azimuth = ds["azimuth"].values.astype(np.float64)
        else:
            continue

        if "range" in ds.coords:
            range_m = ds.coords["range"].values.astype(np.float64)
        elif "range" in ds.data_vars:
            range_m = ds["range"].values.astype(np.float64)
        else:
            continue

        values = da.values.astype(np.float64)
        if values.ndim != 2:
            continue

        x, y, z = _polar_to_cartesian(azimuth, range_m, elev_mean)

        # Broad bounding-box filter first
        mask = (
            (x >= box_x_min) & (x <= box_x_max)
            & (y >= box_y_min) & (y <= box_y_max)
            & (z >= z_min_val) & (z <= z_max)
            & np.isfinite(values)
        )
        if not np.any(mask):
            continue

        xm = x[mask]
        ym = y[mask]
        zm = z[mask]
        vm = values[mask]

        # Project onto line: d = dot((p - start), u_hat)
        d_along = (xm - sx) * ux + (ym - sy) * uy
        d_perp = np.abs((xm - sx) * px + (ym - sy) * py)

        # Filter to corridor
        corridor = (d_along >= 0) & (d_along <= line_len) & (d_perp <= half_w)
        if not np.any(corridor):
            continue

        all_d.append(d_along[corridor])
        all_z.append(zm[corridor])
        all_v.append(vm[corridor])

    n_dist = n_horizontal
    n_z = n_vertical

    if not all_d:
        empty = np.full(n_dist * n_z, NODATA, dtype=np.float32)
        return {
            "data": empty,
            "n_dist": n_dist, "n_z": n_z,
            "dist_min": 0.0, "dist_max": float(line_len),
            "z_min": z_min_val, "z_max": float(z_max),
            "vmin": 0.0, "vmax": 0.0,
        }

    pts_d = np.concatenate(all_d)
    pts_z = np.concatenate(all_z)
    pts_v = np.concatenate(all_v)

    logger.info(
        "Cross-section 3D: %d scattered points -> (%d, %d) grid",
        len(pts_v), n_dist, n_z,
    )

    gd = np.linspace(0, line_len, n_dist)
    gz = np.linspace(z_min_val, z_max, n_z)
    gd2, gz2 = np.meshgrid(gd, gz, indexing="ij")  # shape (n_dist, n_z)

    points = np.column_stack((pts_d, pts_z))
    interp = LinearNDInterpolator(points, pts_v, fill_value=np.nan)
    grid = interp(gd2, gz2)

    valid = grid[np.isfinite(grid)]
    if len(valid) > 0:
        vmin = float(np.nanmin(valid))
        vmax = float(np.nanmax(valid))
    else:
        vmin = 0.0
        vmax = 0.0

    grid = np.nan_to_num(grid, nan=NODATA).astype(np.float32)
    flat = grid.ravel(order="C")

    return {
        "data": flat,
        "n_dist": n_dist, "n_z": n_z,
        "dist_min": 0.0, "dist_max": float(line_len),
        "z_min": z_min_val, "z_max": float(z_max),
        "vmin": vmin, "vmax": vmax,
    }
