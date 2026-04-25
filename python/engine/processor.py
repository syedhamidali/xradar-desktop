"""Radar data processing pipeline with cross-section and profile extraction."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

# Type alias for the progress callback: (percent: int, message: str) -> None
ProgressCallback = Callable[[int, str], None]


def _noop_progress(percent: int, message: str) -> None:
    """Default progress callback that does nothing."""


class RadarProcessor:
    """Apply processing steps to radar data.

    Phase 1 provides stub implementations that pass data through unchanged
    while logging what *would* be done. Each method accepts an optional
    progress callback so the server can relay status to the frontend.
    """

    def process(
        self,
        datatree: xr.DataTree,
        pipeline_config: dict[str, Any],
        progress: ProgressCallback = _noop_progress,
    ) -> xr.DataTree:
        """Run the configured pipeline on *datatree* and return the result.

        Parameters
        ----------
        datatree : xr.DataTree
            The radar datatree to process.
        pipeline_config : dict
            Keys are processing step names; values are booleans or config
            dicts.  Recognised keys (Phase 1):
            - ``despeckle`` (bool)
            - ``dealias`` (bool)
            - ``gridding`` (str — ``"none"``, ``"grid"``, etc.)
        progress : ProgressCallback
            Called with ``(percent, message)`` as processing advances.

        Returns
        -------
        xr.DataTree
            Processed datatree (in Phase 1, returned unmodified).
        """
        steps = self._plan_steps(pipeline_config)
        total = len(steps) if steps else 1

        progress(0, "Starting processing pipeline")

        for idx, (name, config) in enumerate(steps):
            pct = int(((idx + 1) / total) * 100)
            progress(pct, f"Running {name}...")
            datatree = self._run_step(name, datatree, config)

        progress(100, "Processing complete")
        return datatree

    # ------------------------------------------------------------------
    # Individual processing steps (Phase 1 stubs)
    # ------------------------------------------------------------------

    def despeckle(
        self,
        data: xr.DataTree,
        progress: ProgressCallback = _noop_progress,
    ) -> xr.DataTree:
        """Remove speckle noise from radar data.

        Phase 1: stub — returns data unchanged.
        """
        logger.info(
            "[stub] despeckle called — would apply speckle filter to %d sweeps",
            len([n for n in data.children if n.startswith("sweep_")]),
        )
        progress(100, "Despeckle complete (stub)")
        return data

    def dealias_velocity(
        self,
        data: xr.DataTree,
        progress: ProgressCallback = _noop_progress,
    ) -> xr.DataTree:
        """Dealias radial velocity data.

        Phase 1: stub — returns data unchanged.
        """
        logger.info(
            "[stub] dealias_velocity called — would dealias velocity in %d sweeps",
            len([n for n in data.children if n.startswith("sweep_")]),
        )
        progress(100, "Velocity dealiasing complete (stub)")
        return data

    def grid_data(
        self,
        data: xr.DataTree,
        method: str = "none",
        progress: ProgressCallback = _noop_progress,
    ) -> xr.DataTree:
        """Grid polar radar data to a Cartesian grid.

        Phase 1: stub — returns data unchanged.

        Parameters
        ----------
        method : str
            Gridding method.  ``"none"`` skips gridding.
        """
        if method == "none":
            logger.info("[stub] grid_data called with method='none' — skipping")
            progress(100, "Gridding skipped")
            return data

        logger.info(
            "[stub] grid_data called with method='%s' — would grid %d sweeps",
            method,
            len([n for n in data.children if n.startswith("sweep_")]),
        )
        progress(100, f"Gridding ({method}) complete (stub)")
        return data

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _plan_steps(
        self, config: dict[str, Any]
    ) -> list[tuple[str, Any]]:
        """Determine which steps to run and in what order."""
        steps: list[tuple[str, Any]] = []
        if config.get("despeckle"):
            steps.append(("despeckle", True))
        if config.get("dealias"):
            steps.append(("dealias", True))
        gridding = config.get("gridding", "none")
        if gridding and gridding != "none":
            steps.append(("gridding", gridding))
        return steps

    def _run_step(
        self,
        name: str,
        datatree: xr.DataTree,
        config: Any,
    ) -> xr.DataTree:
        """Dispatch a single processing step by *name*."""
        if name == "despeckle":
            return self.despeckle(datatree)
        if name == "dealias":
            return self.dealias_velocity(datatree)
        if name == "gridding":
            method = config if isinstance(config, str) else "none"
            return self.grid_data(datatree, method=method)

        logger.warning("Unknown processing step '%s' — skipping", name)
        return datatree


# ======================================================================
# Cross-section and vertical profile extraction
# ======================================================================

def _get_sweep_data(
    datatree: xr.DataTree,
    sweep_idx: int,
    variable: str,
) -> tuple[xr.DataArray | None, float, np.ndarray, np.ndarray]:
    """Extract a variable from a sweep, returning (data, elev_deg, azimuth, range).

    Returns (None, 0, [], []) if the variable is not in the sweep.
    """
    sweep_names = sorted(n for n in datatree.children if n.startswith("sweep_"))
    if sweep_idx >= len(sweep_names):
        return None, 0.0, np.array([]), np.array([])

    ds = datatree[sweep_names[sweep_idx]].to_dataset()
    if variable not in ds.data_vars:
        return None, 0.0, np.array([]), np.array([])

    da = ds[variable]

    # Elevation
    elev = 0.0
    if "sweep_fixed_angle" in ds.attrs:
        elev = float(ds.attrs["sweep_fixed_angle"])
    elif "sweep_fixed_angle" in ds:
        elev = float(ds["sweep_fixed_angle"].values)
    elif "elevation" in ds:
        elev = float(np.nanmean(ds["elevation"].values))

    azimuth = da.coords["azimuth"].values if "azimuth" in da.coords else np.arange(da.shape[0], dtype=np.float64)
    range_m = da.coords["range"].values if "range" in da.coords else np.arange(da.shape[-1], dtype=np.float64)

    return da, elev, azimuth.astype(np.float64), range_m.astype(np.float64)


def _polar_to_xy(azimuth_deg: float, range_m: float) -> tuple[float, float]:
    """Convert polar (azimuth, range) to local (x, y) in metres.

    Azimuth is clockwise from north.
    """
    az_rad = np.radians(azimuth_deg)
    x = range_m * np.sin(az_rad)
    y = range_m * np.cos(az_rad)
    return float(x), float(y)


def extract_cross_section(
    datatree: xr.DataTree,
    start_point: tuple[float, float],
    end_point: tuple[float, float],
    variable: str,
    *,
    n_points: int = 200,
) -> dict[str, Any]:
    """Extract an RHI-like cross-section between two geographic points.

    The cross-section interpolates across all sweeps to produce a
    distance-vs-height slice.

    Parameters
    ----------
    datatree : xr.DataTree
        Radar DataTree.
    start_point : tuple[float, float]
        Start (latitude, longitude) in degrees.
    end_point : tuple[float, float]
        End (latitude, longitude) in degrees.
    variable : str
        Variable name (e.g. ``"DBZH"``).
    n_points : int
        Number of horizontal sample points.

    Returns
    -------
    dict
        Keys: ``distance_km`` (list), ``height_km`` (list of lists per
        sweep), ``values`` (list of lists per sweep), ``variable`` (str),
        ``units`` (str), ``n_sweeps`` (int).
    """
    sweep_names = sorted(n for n in datatree.children if n.startswith("sweep_"))
    n_sweeps = len(sweep_names)

    # Compute azimuth and range along the line from radar to each sample point.
    # Approximate: treat lat/lon as local offsets from radar origin.
    # Get radar location from root attributes or first sweep.
    radar_lat, radar_lon = _get_radar_location(datatree)

    lat1, lon1 = start_point
    lat2, lon2 = end_point

    # Sample points along the line
    lats = np.linspace(lat1, lat2, n_points)
    lons = np.linspace(lon1, lon2, n_points)

    # Convert to local coordinates (approximate metres from radar)
    dx = (lons - radar_lon) * 111320.0 * np.cos(np.radians(radar_lat))
    dy = (lats - radar_lat) * 110540.0

    # Compute azimuth and range from radar for each sample point
    sample_range = np.sqrt(dx ** 2 + dy ** 2)
    sample_azimuth = np.degrees(np.arctan2(dx, dy)) % 360.0

    # Distance along the cross-section line
    total_dx = dx[-1] - dx[0]
    total_dy = dy[-1] - dy[0]
    total_dist = np.sqrt(total_dx ** 2 + total_dy ** 2)
    distance_km = np.linspace(0, total_dist / 1000.0, n_points)

    re_eff = 8_493_000.0  # 4/3 earth radius in metres
    all_heights: list[list[float]] = []
    all_values: list[list[float]] = []
    units = ""

    for si in range(n_sweeps):
        da, elev_deg, sweep_az, sweep_rng = _get_sweep_data(datatree, si, variable)
        if da is None:
            continue

        if not units and "units" in da.attrs:
            units = str(da.attrs["units"])

        elev_rad = np.radians(elev_deg)
        values_2d = da.values.astype(np.float64)

        # For each sample point, find nearest azimuth and interpolate in range
        row_values: list[float] = []
        row_heights: list[float] = []

        for pi in range(n_points):
            r = sample_range[pi]
            az = sample_azimuth[pi]

            # Height at this range and elevation
            h = r * np.sin(elev_rad) + (r ** 2) / (2.0 * re_eff)
            row_heights.append(h / 1000.0)

            # Find nearest azimuth index
            az_diff = np.abs(sweep_az - az)
            az_diff = np.minimum(az_diff, 360.0 - az_diff)
            az_idx = int(np.argmin(az_diff))

            # Interpolate in range
            if r < sweep_rng[0] or r > sweep_rng[-1]:
                row_values.append(float("nan"))
                continue

            rng_idx = np.searchsorted(sweep_rng, r)
            if rng_idx == 0:
                val = float(values_2d[az_idx, 0])
            elif rng_idx >= len(sweep_rng):
                val = float(values_2d[az_idx, -1])
            else:
                # Linear interpolation between adjacent range gates
                r0 = sweep_rng[rng_idx - 1]
                r1 = sweep_rng[rng_idx]
                v0 = float(values_2d[az_idx, rng_idx - 1])
                v1 = float(values_2d[az_idx, rng_idx])
                frac = (r - r0) / (r1 - r0) if r1 != r0 else 0.0
                val = v0 + frac * (v1 - v0)

            row_values.append(val)

        all_heights.append(row_heights)
        all_values.append(row_values)

    return {
        "distance_km": distance_km.tolist(),
        "height_km": all_heights,
        "values": all_values,
        "variable": variable,
        "units": units,
        "n_sweeps": len(all_heights),
    }


def extract_vertical_profile(
    datatree: xr.DataTree,
    lat: float,
    lon: float,
    variable: str,
) -> dict[str, Any]:
    """Extract a vertical profile (column) at a geographic point.

    Interpolates the variable at the nearest gate for each sweep,
    producing a profile of value vs. height.

    Parameters
    ----------
    datatree : xr.DataTree
        Radar DataTree.
    lat, lon : float
        Geographic coordinates of the point.
    variable : str
        Variable name.

    Returns
    -------
    dict
        Keys: ``heights_km`` (list), ``values`` (list),
        ``elevations_deg`` (list), ``variable`` (str), ``units`` (str).
    """
    sweep_names = sorted(n for n in datatree.children if n.startswith("sweep_"))
    radar_lat, radar_lon = _get_radar_location(datatree)

    # Convert point to local coords
    dx = (lon - radar_lon) * 111320.0 * np.cos(np.radians(radar_lat))
    dy = (lat - radar_lat) * 110540.0
    point_range = np.sqrt(dx ** 2 + dy ** 2)
    point_az = np.degrees(np.arctan2(dx, dy)) % 360.0

    re_eff = 8_493_000.0
    heights: list[float] = []
    values: list[float] = []
    elevations: list[float] = []
    units = ""

    for si in range(len(sweep_names)):
        da, elev_deg, sweep_az, sweep_rng = _get_sweep_data(datatree, si, variable)
        if da is None:
            continue

        if not units and "units" in da.attrs:
            units = str(da.attrs["units"])

        elev_rad = np.radians(elev_deg)
        h = point_range * np.sin(elev_rad) + (point_range ** 2) / (2.0 * re_eff)

        values_2d = da.values.astype(np.float64)

        # Nearest azimuth
        az_diff = np.abs(sweep_az - point_az)
        az_diff = np.minimum(az_diff, 360.0 - az_diff)
        az_idx = int(np.argmin(az_diff))

        # Interpolate in range
        if point_range < sweep_rng[0] or point_range > sweep_rng[-1]:
            val = float("nan")
        else:
            rng_idx = np.searchsorted(sweep_rng, point_range)
            if rng_idx == 0:
                val = float(values_2d[az_idx, 0])
            elif rng_idx >= len(sweep_rng):
                val = float(values_2d[az_idx, -1])
            else:
                r0 = sweep_rng[rng_idx - 1]
                r1 = sweep_rng[rng_idx]
                v0 = float(values_2d[az_idx, rng_idx - 1])
                v1 = float(values_2d[az_idx, rng_idx])
                frac = (point_range - r0) / (r1 - r0) if r1 != r0 else 0.0
                val = v0 + frac * (v1 - v0)

        heights.append(h / 1000.0)
        values.append(val)
        elevations.append(elev_deg)

    return {
        "heights_km": heights,
        "values": values,
        "elevations_deg": elevations,
        "variable": variable,
        "units": units,
    }


def _get_radar_location(datatree: xr.DataTree) -> tuple[float, float]:
    """Extract radar latitude/longitude from a DataTree.

    Checks root attributes and first sweep for common coordinate names.
    Returns (0.0, 0.0) if not found.
    """
    # Check root attributes
    try:
        root_ds = datatree.to_dataset()
        for lat_key in ("latitude", "origin_latitude", "siteLat"):
            if lat_key in root_ds:
                lat = float(np.asarray(root_ds[lat_key]).flat[0])
                for lon_key in ("longitude", "origin_longitude", "siteLon"):
                    if lon_key in root_ds:
                        lon = float(np.asarray(root_ds[lon_key]).flat[0])
                        return lat, lon
            if lat_key in root_ds.attrs:
                lat = float(root_ds.attrs[lat_key])
                for lon_key in ("longitude", "origin_longitude", "siteLon"):
                    if lon_key in root_ds.attrs:
                        lon = float(root_ds.attrs[lon_key])
                        return lat, lon
    except Exception:
        pass

    # Check first sweep
    sweep_names = sorted(n for n in datatree.children if n.startswith("sweep_"))
    if sweep_names:
        try:
            ds = datatree[sweep_names[0]].to_dataset()
            for lat_key in ("latitude", "origin_latitude", "siteLat"):
                if lat_key in ds:
                    lat = float(np.asarray(ds[lat_key]).flat[0])
                    for lon_key in ("longitude", "origin_longitude", "siteLon"):
                        if lon_key in ds:
                            lon = float(np.asarray(ds[lon_key]).flat[0])
                            return lat, lon
        except Exception:
            pass

    logger.warning("Could not determine radar location; defaulting to (0, 0)")
    return 0.0, 0.0
