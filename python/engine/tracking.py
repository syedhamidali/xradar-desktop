"""Storm cell identification and tracking for radar data.

Provides connected-component cell detection on reflectivity fields and
simple nearest-neighbor tracking between consecutive scans.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import xarray as xr
from scipy import ndimage

logger = logging.getLogger(__name__)


@dataclass
class CellInfo:
    """Properties of a single identified storm cell."""

    id: str
    centroid_az: float          # degrees
    centroid_range: float       # metres
    area_km2: float
    max_dbz: float
    mean_dbz: float
    boundary_points: list[list[float]]  # [[az_deg, range_m], ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        return {
            "id": self.id,
            "centroid_az": round(self.centroid_az, 2),
            "centroid_range": round(self.centroid_range, 2),
            "area_km2": round(self.area_km2, 2),
            "max_dbz": round(self.max_dbz, 1),
            "mean_dbz": round(self.mean_dbz, 1),
            "boundary_points": [
                [round(az, 2), round(rng, 2)] for az, rng in self.boundary_points
            ],
        }


@dataclass
class CellTrack:
    """A tracked storm cell across consecutive scans."""

    track_id: str
    cell_history: list[CellInfo] = field(default_factory=list)
    speed_mps: float = 0.0       # metres per second
    direction_deg: float = 0.0   # meteorological convention (from-direction)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        return {
            "track_id": self.track_id,
            "cell_history": [c.to_dict() for c in self.cell_history],
            "speed_mps": round(self.speed_mps, 2),
            "direction_deg": round(self.direction_deg, 1),
        }


# ---------------------------------------------------------------------------
# Cell identification
# ---------------------------------------------------------------------------

def identify_cells(
    data: xr.DataArray,
    threshold_dbz: float = 35.0,
    min_size_km2: float = 10.0,
) -> list[CellInfo]:
    """Identify storm cells in a reflectivity sweep using connected-component labelling.

    Parameters
    ----------
    data : xr.DataArray
        2-D reflectivity array with coordinates ``azimuth`` (degrees) and
        ``range`` (metres).  NaN values are treated as below threshold.
    threshold_dbz : float
        Minimum reflectivity value (dBZ) to include a gate in a cell.
    min_size_km2 : float
        Cells with area below this value are discarded.

    Returns
    -------
    list[CellInfo]
        Identified cells sorted by descending ``max_dbz``.
    """
    values = np.asarray(data.values, dtype=np.float64)
    azimuth = np.asarray(data.coords["azimuth"].values, dtype=np.float64)
    range_m = np.asarray(data.coords["range"].values, dtype=np.float64)

    # Compute gate spacing for area estimation
    if len(range_m) >= 2:
        gate_spacing_m = float(np.median(np.diff(range_m)))
    else:
        gate_spacing_m = float(range_m[0]) if len(range_m) == 1 else 250.0

    n_az = len(azimuth)
    if n_az >= 2:
        az_spacing_deg = float(np.median(np.diff(np.sort(azimuth))))
    else:
        az_spacing_deg = 1.0

    # Binary mask: above threshold and not NaN
    mask = np.where(np.isnan(values), False, values >= threshold_dbz)

    # Connected-component labelling (8-connectivity)
    structure = ndimage.generate_binary_structure(2, 2)
    labels, n_features = ndimage.label(mask, structure=structure)

    if n_features == 0:
        logger.info("No cells found above %.1f dBZ", threshold_dbz)
        return []

    cells: list[CellInfo] = []

    for label_id in range(1, n_features + 1):
        component_mask = labels == label_id
        az_indices, rng_indices = np.where(component_mask)

        if len(az_indices) == 0:
            continue

        # Estimate area: each gate is approximately gate_spacing * (range * az_spacing_rad)
        # For a more accurate estimate, weight by range.
        component_ranges = range_m[rng_indices]
        az_spacing_rad = math.radians(az_spacing_deg)
        gate_areas_m2 = component_ranges * az_spacing_rad * gate_spacing_m
        total_area_km2 = float(np.sum(gate_areas_m2)) / 1e6

        if total_area_km2 < min_size_km2:
            continue

        # Cell statistics
        cell_values = values[component_mask]
        max_dbz = float(np.nanmax(cell_values))
        mean_dbz = float(np.nanmean(cell_values))

        # Centroid (area-weighted)
        centroid_az = float(np.average(azimuth[az_indices], weights=gate_areas_m2))
        centroid_range = float(np.average(range_m[rng_indices], weights=gate_areas_m2))

        # Boundary extraction: find edge pixels of the component
        boundary_points = _extract_boundary(
            component_mask, azimuth, range_m, max_points=120,
        )

        cell_id = f"C{label_id:03d}"
        cells.append(CellInfo(
            id=cell_id,
            centroid_az=centroid_az,
            centroid_range=centroid_range,
            area_km2=total_area_km2,
            max_dbz=max_dbz,
            mean_dbz=mean_dbz,
            boundary_points=boundary_points,
        ))

    # Sort by descending max dBZ
    cells.sort(key=lambda c: c.max_dbz, reverse=True)

    # Re-label IDs by strength rank
    for i, cell in enumerate(cells):
        cell.id = f"C{i + 1:03d}"

    logger.info(
        "Identified %d cells above %.1f dBZ (min area %.1f km2)",
        len(cells), threshold_dbz, min_size_km2,
    )
    return cells


def _extract_boundary(
    mask: np.ndarray,
    azimuth: np.ndarray,
    range_m: np.ndarray,
    max_points: int = 120,
) -> list[list[float]]:
    """Extract boundary pixels from a binary mask and return as [az, range] pairs.

    Uses morphological erosion to find edge pixels, then sub-samples to
    *max_points* evenly spaced around the boundary.
    """
    eroded = ndimage.binary_erosion(mask)
    edge = mask & ~eroded

    az_idx, rng_idx = np.where(edge)
    if len(az_idx) == 0:
        # Fallback: component is only one pixel thick
        az_idx, rng_idx = np.where(mask)

    points = np.column_stack([azimuth[az_idx], range_m[rng_idx]])

    if len(points) <= max_points:
        return points.tolist()

    # Sub-sample evenly by sorting by angle relative to centroid then picking
    centroid_az = np.mean(points[:, 0])
    centroid_rng = np.mean(points[:, 1])
    angles = np.arctan2(
        points[:, 1] - centroid_rng,
        points[:, 0] - centroid_az,
    )
    order = np.argsort(angles)
    step = max(1, len(order) // max_points)
    sampled = points[order[::step]]
    return sampled.tolist()


# ---------------------------------------------------------------------------
# Cell tracking (nearest-neighbour)
# ---------------------------------------------------------------------------

def _cell_distance_km(a: CellInfo, b: CellInfo) -> float:
    """Approximate distance in km between two cell centroids.

    Uses polar coordinates: each cell has (azimuth_deg, range_m).
    Distance computed via the law of cosines on the slant-range plane.
    """
    r1 = a.centroid_range
    r2 = b.centroid_range
    daz = math.radians(b.centroid_az - a.centroid_az)
    dist_m = math.sqrt(r1**2 + r2**2 - 2 * r1 * r2 * math.cos(daz))
    return dist_m / 1000.0


def track_cells(
    cells_t0: list[CellInfo],
    cells_t1: list[CellInfo],
    max_distance_km: float = 30.0,
    dt_seconds: float = 300.0,
) -> list[CellTrack]:
    """Match cells between two consecutive scans using nearest-neighbour assignment.

    Parameters
    ----------
    cells_t0 : list[CellInfo]
        Cells from the earlier scan.
    cells_t1 : list[CellInfo]
        Cells from the later scan.
    max_distance_km : float
        Maximum allowed distance for a match.
    dt_seconds : float
        Time elapsed between the two scans (used for speed calculation).

    Returns
    -------
    list[CellTrack]
        One track per matched pair, plus new tracks for unmatched t1 cells.
    """
    tracks: list[CellTrack] = []
    used_t1: set[int] = set()

    for c0 in cells_t0:
        best_idx: int | None = None
        best_dist = max_distance_km

        for j, c1 in enumerate(cells_t1):
            if j in used_t1:
                continue
            dist = _cell_distance_km(c0, c1)
            if dist < best_dist:
                best_dist = dist
                best_idx = j

        if best_idx is not None:
            c1 = cells_t1[best_idx]
            used_t1.add(best_idx)

            # Compute motion vector
            speed_mps, direction_deg = _compute_motion(c0, c1, dt_seconds)

            track = CellTrack(
                track_id=f"T{c0.id[1:]}",
                cell_history=[c0, c1],
                speed_mps=speed_mps,
                direction_deg=direction_deg,
            )
            tracks.append(track)
        else:
            # Cell from t0 with no match — ended
            tracks.append(CellTrack(
                track_id=f"T{c0.id[1:]}",
                cell_history=[c0],
            ))

    # Unmatched t1 cells — new cells
    for j, c1 in enumerate(cells_t1):
        if j not in used_t1:
            tracks.append(CellTrack(
                track_id=f"TN{j + 1:02d}",
                cell_history=[c1],
            ))

    logger.info(
        "Tracked %d cells (%d matched, %d new)",
        len(tracks),
        len(used_t1),
        len(cells_t1) - len(used_t1),
    )
    return tracks


def _compute_motion(
    c0: CellInfo,
    c1: CellInfo,
    dt_seconds: float,
) -> tuple[float, float]:
    """Compute motion vector (speed in m/s, direction in degrees).

    Direction follows meteorological convention: the direction *from which*
    the cell is moving.
    """
    if dt_seconds <= 0:
        return 0.0, 0.0

    # Convert polar to approximate Cartesian (metres)
    az0 = math.radians(c0.centroid_az)
    az1 = math.radians(c1.centroid_az)
    x0 = c0.centroid_range * math.sin(az0)
    y0 = c0.centroid_range * math.cos(az0)
    x1 = c1.centroid_range * math.sin(az1)
    y1 = c1.centroid_range * math.cos(az1)

    dx = x1 - x0
    dy = y1 - y0
    dist_m = math.sqrt(dx**2 + dy**2)
    speed_mps = dist_m / dt_seconds

    # Direction: where the cell is moving *to* (heading), then convert
    # to meteorological "from" direction.
    heading_deg = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    from_deg = (heading_deg + 180) % 360

    return speed_mps, from_deg


def extrapolate_cell(
    cell: CellInfo,
    speed_mps: float,
    direction_deg: float,
    lead_time_seconds: float,
) -> tuple[float, float]:
    """Extrapolate a cell's centroid position forward in time.

    Parameters
    ----------
    cell : CellInfo
        Current cell position.
    speed_mps : float
        Motion speed in m/s.
    direction_deg : float
        Meteorological "from" direction in degrees.
    lead_time_seconds : float
        Forecast lead time in seconds.

    Returns
    -------
    tuple[float, float]
        Predicted (azimuth_deg, range_m).
    """
    # "From" direction → heading (to-direction)
    heading_rad = math.radians((direction_deg + 180) % 360)
    dist_m = speed_mps * lead_time_seconds

    # Current position in Cartesian
    az_rad = math.radians(cell.centroid_az)
    x = cell.centroid_range * math.sin(az_rad) + dist_m * math.sin(heading_rad)
    y = cell.centroid_range * math.cos(az_rad) + dist_m * math.cos(heading_rad)

    # Back to polar
    pred_range = math.sqrt(x**2 + y**2)
    pred_az = (math.degrees(math.atan2(x, y)) + 360) % 360

    return pred_az, pred_range
