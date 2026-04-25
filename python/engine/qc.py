"""Data quality control (QC) pipeline for radar data.

Provides individual QC algorithms and a composable pipeline builder
that chains multiple steps in user-defined order.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import xarray as xr
from scipy import ndimage

logger = logging.getLogger(__name__)


# ======================================================================
# Individual QC algorithms
# ======================================================================


def despeckle(
    data: xr.DataArray,
    window_size: int = 3,
    threshold: float = 0.5,
) -> xr.DataArray:
    """Remove isolated pixels using a median filter.

    Pixels whose value deviates from the local median by more than
    *threshold* times the local standard deviation are replaced by NaN.

    Parameters
    ----------
    data : xr.DataArray
        2-D radar field (azimuth x range).
    window_size : int
        Side length of the square median-filter kernel.
    threshold : float
        Number of local standard deviations to tolerate before masking.
    """
    values = data.values.astype(np.float64)
    mask = np.isnan(values)
    filled = np.where(mask, 0.0, values)

    median = ndimage.median_filter(filled, size=window_size)
    local_std = ndimage.generic_filter(filled, np.nanstd, size=window_size)
    local_std = np.where(local_std == 0, 1.0, local_std)

    deviation = np.abs(filled - median) / local_std
    speckle = deviation > threshold
    result = np.where(speckle & ~mask, np.nan, values)

    out = data.copy(data=result)
    out.attrs = {**data.attrs, "qc_despeckle": f"window={window_size}, threshold={threshold}"}
    return out


def remove_clutter(
    data: xr.DataArray,
    texture_threshold: float = 0.3,
    rhohv_threshold: float = 0.7,
    *,
    rhohv: xr.DataArray | None = None,
) -> xr.DataArray:
    """Mask ground clutter using texture and optional RhoHV thresholding.

    Clutter is identified where the local texture (standard deviation in a
    3x3 window) exceeds *texture_threshold* **or** where RhoHV is below
    *rhohv_threshold* (if RhoHV data is available).

    Parameters
    ----------
    data : xr.DataArray
        Primary radar field.
    texture_threshold : float
        Maximum tolerated local-texture value.
    rhohv_threshold : float
        Minimum RhoHV for meteorological echoes.
    rhohv : xr.DataArray, optional
        Co-polar correlation coefficient field (same grid as *data*).
    """
    values = data.values.astype(np.float64)
    mask = np.isnan(values)
    filled = np.where(mask, 0.0, values)

    texture = ndimage.generic_filter(filled, np.nanstd, size=3)
    # Normalise texture to [0, 1] for thresholding
    tex_max = np.nanmax(texture)
    if tex_max > 0:
        texture = texture / tex_max

    clutter = texture > texture_threshold

    if rhohv is not None:
        rhv = rhohv.values.astype(np.float64)
        clutter = clutter | (rhv < rhohv_threshold)

    result = np.where(clutter & ~mask, np.nan, values)
    out = data.copy(data=result)
    out.attrs = {
        **data.attrs,
        "qc_clutter_removal": f"texture={texture_threshold}, rhohv={rhohv_threshold}",
    }
    return out


def velocity_dealiasing(
    data: xr.DataArray,
    nyquist_vel: float,
) -> xr.DataArray:
    """Simple region-based velocity dealiasing.

    Uses a 2-pass approach: first corrects obvious folds (values near
    +-Nyquist boundary) using neighbour context, then smooths residual
    discontinuities.

    Parameters
    ----------
    data : xr.DataArray
        Radial velocity field.
    nyquist_vel : float
        Nyquist velocity (m/s) — half the unambiguous velocity interval.
    """
    values = data.values.astype(np.float64).copy()
    mask = np.isnan(values)
    interval = 2.0 * nyquist_vel

    # Pass 1: correct obvious folds using local median comparison
    filled = np.where(mask, 0.0, values)
    local_med = ndimage.median_filter(filled, size=5)

    diff = values - local_med
    n_folds = np.round(diff / interval)
    values = values - n_folds * interval

    # Pass 2: clamp any remaining out-of-range values
    values = np.where(
        (~mask) & (np.abs(values) > nyquist_vel),
        values - np.sign(values) * interval,
        values,
    )
    values = np.where(mask, np.nan, values)

    out = data.copy(data=values)
    out.attrs = {**data.attrs, "qc_dealiased": f"nyquist_vel={nyquist_vel}"}
    return out


def noise_removal(
    data: xr.DataArray,
    noise_floor_dbz: float = -10.0,
) -> xr.DataArray:
    """Mask values below the noise floor.

    Parameters
    ----------
    data : xr.DataArray
        Radar field (typically reflectivity in dBZ).
    noise_floor_dbz : float
        Threshold below which values are considered noise.
    """
    values = data.values.astype(np.float64)
    result = np.where(values < noise_floor_dbz, np.nan, values)
    out = data.copy(data=result)
    out.attrs = {**data.attrs, "qc_noise_removal": f"floor={noise_floor_dbz}"}
    return out


def range_correction(
    data: xr.DataArray,
    range_m: np.ndarray | None = None,
) -> xr.DataArray:
    """Apply 1/r^2 range correction.

    Compensates for the decrease in returned power with range by adding
    ``20 * log10(r / r_ref)`` dB, where *r_ref* is the first range gate.

    Parameters
    ----------
    data : xr.DataArray
        Radar reflectivity field.
    range_m : ndarray, optional
        Range values in metres.  If *None*, extracted from ``data.coords["range"]``.
    """
    if range_m is None:
        if "range" in data.coords:
            range_m = data.coords["range"].values.astype(np.float64)
        else:
            logger.warning("range_correction: no range coordinate found; returning data unchanged")
            return data

    r = range_m.copy()
    r[r <= 0] = r[r > 0].min() if np.any(r > 0) else 1.0
    r_ref = r[0]

    correction = 20.0 * np.log10(r / r_ref)
    # Broadcast along range dimension (assumed last axis)
    values = data.values.astype(np.float64) + correction[np.newaxis, :]

    out = data.copy(data=values)
    out.attrs = {**data.attrs, "qc_range_correction": "1/r^2"}
    return out


def smooth_data(
    data: xr.DataArray,
    method: str = "gaussian",
    sigma: float = 1.0,
) -> xr.DataArray:
    """Apply Gaussian or median smoothing.

    Parameters
    ----------
    data : xr.DataArray
        2-D radar field.
    method : str
        ``"gaussian"`` or ``"median"``.
    sigma : float
        For Gaussian: standard deviation.  For median: kernel size.
    """
    values = data.values.astype(np.float64)
    mask = np.isnan(values)
    filled = np.where(mask, 0.0, values)

    if method == "gaussian":
        smoothed = ndimage.gaussian_filter(filled, sigma=sigma)
    elif method == "median":
        smoothed = ndimage.median_filter(filled, size=max(int(sigma), 3))
    else:
        logger.warning("smooth_data: unknown method '%s'; returning data unchanged", method)
        return data

    result = np.where(mask, np.nan, smoothed)
    out = data.copy(data=result)
    out.attrs = {**data.attrs, "qc_smoothed": f"{method}, sigma={sigma}"}
    return out


def threshold_filter(
    data: xr.DataArray,
    vmin: float | None = None,
    vmax: float | None = None,
) -> xr.DataArray:
    """Mask values outside [vmin, vmax].

    Parameters
    ----------
    data : xr.DataArray
        Radar field.
    vmin, vmax : float, optional
        Lower / upper threshold.  ``None`` means no bound on that side.
    """
    values = data.values.astype(np.float64)
    mask = np.zeros_like(values, dtype=bool)
    if vmin is not None:
        mask |= values < vmin
    if vmax is not None:
        mask |= values > vmax

    result = np.where(mask, np.nan, values)
    out = data.copy(data=result)
    out.attrs = {**data.attrs, "qc_threshold": f"vmin={vmin}, vmax={vmax}"}
    return out


# ======================================================================
# QC Algorithm Registry
# ======================================================================

QC_REGISTRY: dict[str, dict[str, Any]] = {
    "despeckle": {
        "function": despeckle,
        "description": "Remove isolated noisy pixels using median filter comparison",
        "params": {
            "window_size": {
                "type": "int",
                "default": 3,
                "label": "Window size",
                "min": 3,
                "max": 11,
            },
            "threshold": {
                "type": "float",
                "default": 0.5,
                "label": "Threshold (std devs)",
                "min": 0.1,
                "max": 5.0,
            },
        },
        "applicable_variables": [
            "DBZH",
            "DBZ",
            "ZH",
            "DBZV",
            "ZDR",
            "KDP",
            "PHIDP",
            "RHOHV",
            "VRADH",
            "VRAD",
        ],
    },
    "remove_clutter": {
        "function": remove_clutter,
        "description": "Mask ground clutter using texture analysis and RhoHV",
        "params": {
            "texture_threshold": {
                "type": "float",
                "default": 0.3,
                "label": "Texture threshold",
                "min": 0.05,
                "max": 1.0,
            },
            "rhohv_threshold": {
                "type": "float",
                "default": 0.7,
                "label": "RhoHV threshold",
                "min": 0.3,
                "max": 1.0,
            },
        },
        "applicable_variables": ["DBZH", "DBZ", "ZH", "DBZV", "ZDR"],
    },
    "velocity_dealiasing": {
        "function": velocity_dealiasing,
        "description": "Region-based velocity dealiasing using Nyquist interval",
        "params": {
            "nyquist_vel": {
                "type": "float",
                "default": 25.0,
                "label": "Nyquist velocity (m/s)",
                "min": 1.0,
                "max": 100.0,
            },
        },
        "applicable_variables": ["VRADH", "VRAD", "V", "VEL"],
    },
    "noise_removal": {
        "function": noise_removal,
        "description": "Mask values below the noise floor threshold",
        "params": {
            "noise_floor_dbz": {
                "type": "float",
                "default": -10.0,
                "label": "Noise floor (dBZ)",
                "min": -40.0,
                "max": 10.0,
            },
        },
        "applicable_variables": ["DBZH", "DBZ", "ZH", "DBZV"],
    },
    "range_correction": {
        "function": range_correction,
        "description": "Apply 1/r² range correction to compensate power loss with distance",
        "params": {},
        "applicable_variables": ["DBZH", "DBZ", "ZH", "DBZV"],
    },
    "smooth_data": {
        "function": smooth_data,
        "description": "Apply Gaussian or median smoothing to reduce noise",
        "params": {
            "method": {
                "type": "str",
                "default": "gaussian",
                "label": "Method",
                "options": ["gaussian", "median"],
            },
            "sigma": {
                "type": "float",
                "default": 1.0,
                "label": "Sigma / kernel size",
                "min": 0.5,
                "max": 5.0,
            },
        },
        "applicable_variables": [
            "DBZH",
            "DBZ",
            "ZH",
            "DBZV",
            "ZDR",
            "KDP",
            "PHIDP",
            "RHOHV",
            "VRADH",
            "VRAD",
        ],
    },
    "threshold_filter": {
        "function": threshold_filter,
        "description": "Mask values outside a specified min/max range",
        "params": {
            "vmin": {
                "type": "float",
                "default": None,
                "label": "Minimum value",
                "min": -100.0,
                "max": 200.0,
            },
            "vmax": {
                "type": "float",
                "default": None,
                "label": "Maximum value",
                "min": -100.0,
                "max": 200.0,
            },
        },
        "applicable_variables": [
            "DBZH",
            "DBZ",
            "ZH",
            "DBZV",
            "ZDR",
            "KDP",
            "PHIDP",
            "RHOHV",
            "VRADH",
            "VRAD",
        ],
    },
}


# ======================================================================
# QC Pipeline Builder
# ======================================================================


class QCPipeline:
    """Composable QC pipeline that chains multiple steps in order.

    Usage::

        pipeline = QCPipeline()
        pipeline.add_step("despeckle", {"window_size": 5})
        pipeline.add_step("noise_removal", {"noise_floor_dbz": -5})
        cleaned = pipeline.run(data)
    """

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Step management
    # ------------------------------------------------------------------

    def add_step(self, name: str, params: dict[str, Any] | None = None) -> None:
        """Append a QC step to the pipeline.

        Parameters
        ----------
        name : str
            Algorithm name (must be a key in :data:`QC_REGISTRY`).
        params : dict, optional
            Override default parameters for this step.

        Raises
        ------
        ValueError
            If *name* is not in the registry.
        """
        if name not in QC_REGISTRY:
            raise ValueError(f"Unknown QC algorithm: '{name}'. Available: {list(QC_REGISTRY)}")
        self._steps.append({"name": name, "params": params or {}})

    def remove_step(self, index: int) -> None:
        """Remove the step at *index*."""
        if 0 <= index < len(self._steps):
            self._steps.pop(index)
        else:
            raise IndexError(f"Step index {index} out of range (0..{len(self._steps) - 1})")

    def reorder(self, from_idx: int, to_idx: int) -> None:
        """Move a step from *from_idx* to *to_idx*."""
        if not (0 <= from_idx < len(self._steps)):
            raise IndexError(f"from_idx {from_idx} out of range")
        if not (0 <= to_idx < len(self._steps)):
            raise IndexError(f"to_idx {to_idx} out of range")
        step = self._steps.pop(from_idx)
        self._steps.insert(to_idx, step)

    @property
    def steps(self) -> list[dict[str, Any]]:
        return list(self._steps)

    def __len__(self) -> int:
        return len(self._steps)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, data: xr.DataArray) -> xr.DataArray:
        """Apply all pipeline steps in order and return the cleaned DataArray."""
        result = data
        for i, step in enumerate(self._steps):
            name = step["name"]
            params = step["params"]
            entry = QC_REGISTRY.get(name)
            if entry is None:
                logger.warning("Skipping unknown QC step '%s'", name)
                continue

            fn = entry["function"]
            logger.info("QC step %d/%d: %s %s", i + 1, len(self._steps), name, params)
            try:
                result = fn(result, **params)
            except Exception:
                logger.error("QC step '%s' failed", name, exc_info=True)
                raise
        return result

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> list[dict[str, Any]]:
        """Serialise pipeline to a JSON-friendly list."""
        return [{"name": s["name"], "params": s["params"]} for s in self._steps]

    @classmethod
    def from_dict(cls, steps: list[dict[str, Any]]) -> QCPipeline:
        """Deserialise a pipeline from a list of step dicts."""
        pipeline = cls()
        for step in steps:
            pipeline.add_step(step["name"], step.get("params", {}))
        return pipeline
