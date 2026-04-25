"""Radar meteorological retrievals and derived products.

Pure-numpy implementations of common radar retrievals. Each function accepts
an xarray DataArray and returns an xarray DataArray with proper metadata.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# KDP estimation
# ---------------------------------------------------------------------------

def estimate_kdp(
    phidp: xr.DataArray,
    *,
    gate_spacing_m: float | None = None,
    window_len: int = 21,
) -> xr.DataArray:
    """Estimate specific differential phase (KDP) from PhiDP.

    Uses a centred finite-difference method with a moving-average smoother
    applied to PhiDP before differentiation.

    Parameters
    ----------
    phidp : xr.DataArray
        Differential phase (degrees), shape ``(azimuth, range)``.
    gate_spacing_m : float, optional
        Gate spacing in metres. If *None*, inferred from the ``range``
        coordinate.
    window_len : int
        Number of range gates in the smoothing window (must be odd).

    Returns
    -------
    xr.DataArray
        KDP in degrees per kilometre, same shape as *phidp*.
    """
    phi = phidp.values.copy().astype(np.float64)
    mask = np.isnan(phi)

    # Infer gate spacing
    if gate_spacing_m is None:
        if "range" in phidp.coords:
            rng = phidp.coords["range"].values
            gate_spacing_m = float(np.median(np.diff(rng)))
        else:
            gate_spacing_m = 250.0
            logger.warning("No range coord; assuming gate_spacing=%.0f m", gate_spacing_m)

    # Fill NaN for smoothing
    phi[mask] = 0.0

    # Moving average smoothing along range axis
    if window_len < 3:
        window_len = 3
    if window_len % 2 == 0:
        window_len += 1
    kernel = np.ones(window_len) / window_len
    smoothed = np.apply_along_axis(
        lambda row: np.convolve(row, kernel, mode="same"), axis=-1, arr=phi
    )

    # Central finite-difference: KDP = d(PhiDP)/d(range) * 0.5
    # Factor of 0.5 because PhiDP is the two-way differential phase
    kdp = np.gradient(smoothed, gate_spacing_m, axis=-1) * 0.5

    # Convert from deg/m to deg/km
    kdp *= 1000.0

    # Restore NaN mask
    kdp[mask] = np.nan

    result = xr.DataArray(
        kdp.astype(np.float32),
        dims=phidp.dims,
        coords=phidp.coords,
        attrs={
            "units": "degrees/km",
            "long_name": "Specific Differential Phase",
            "standard_name": "specific_differential_phase_hv",
        },
    )
    return result


# ---------------------------------------------------------------------------
# Rainfall rate estimators
# ---------------------------------------------------------------------------

def rain_rate_z(
    dbz: xr.DataArray,
    *,
    a: float = 200.0,
    b: float = 1.6,
) -> xr.DataArray:
    """Estimate rainfall rate from reflectivity using Z = a * R^b.

    Default coefficients are Marshall-Palmer (a=200, b=1.6).

    Parameters
    ----------
    dbz : xr.DataArray
        Reflectivity in dBZ.
    a, b : float
        Z-R relationship coefficients.

    Returns
    -------
    xr.DataArray
        Rainfall rate in mm/h.
    """
    z_linear = 10.0 ** (dbz.values.astype(np.float64) / 10.0)
    rain = (z_linear / a) ** (1.0 / b)
    rain = np.where(np.isnan(dbz.values), np.nan, rain)
    rain = np.clip(rain, 0.0, 500.0)

    return xr.DataArray(
        rain.astype(np.float32),
        dims=dbz.dims,
        coords=dbz.coords,
        attrs={
            "units": "mm/h",
            "long_name": "Rainfall Rate (Z-R)",
            "method": f"Z = {a} * R^{b}",
        },
    )


def rain_rate_kdp(
    kdp: xr.DataArray,
    *,
    a: float = 44.0,
    b: float = 0.822,
) -> xr.DataArray:
    """Estimate rainfall rate from KDP using R = a * KDP^b.

    Default coefficients follow Bringi and Chandrasekar (2001).

    Parameters
    ----------
    kdp : xr.DataArray
        Specific differential phase in deg/km.
    a, b : float
        R-KDP relationship coefficients.

    Returns
    -------
    xr.DataArray
        Rainfall rate in mm/h.
    """
    k = kdp.values.astype(np.float64)
    rain = np.where(k > 0, a * np.power(k, b), 0.0)
    rain = np.where(np.isnan(kdp.values), np.nan, rain)
    rain = np.clip(rain, 0.0, 500.0)

    return xr.DataArray(
        rain.astype(np.float32),
        dims=kdp.dims,
        coords=kdp.coords,
        attrs={
            "units": "mm/h",
            "long_name": "Rainfall Rate (R-KDP)",
            "method": f"R = {a} * KDP^{b}",
        },
    )


def rain_rate_z_zdr(
    dbz: xr.DataArray,
    zdr: xr.DataArray,
    *,
    a: float = 6.7e-3,
    b: float = 0.927,
    c: float = -3.43,
) -> xr.DataArray:
    """Estimate rainfall rate from Z and ZDR.

    R = a * Z_linear^b * 10^(c * ZDR)

    Parameters
    ----------
    dbz : xr.DataArray
        Reflectivity in dBZ.
    zdr : xr.DataArray
        Differential reflectivity in dB.
    a, b, c : float
        Empirical coefficients.

    Returns
    -------
    xr.DataArray
        Rainfall rate in mm/h.
    """
    z_lin = 10.0 ** (dbz.values.astype(np.float64) / 10.0)
    zdr_val = zdr.values.astype(np.float64)
    rain = a * np.power(z_lin, b) * np.power(10.0, c * zdr_val)
    combined_mask = np.isnan(dbz.values) | np.isnan(zdr.values)
    rain = np.where(combined_mask, np.nan, rain)
    rain = np.clip(rain, 0.0, 500.0)

    return xr.DataArray(
        rain.astype(np.float32),
        dims=dbz.dims,
        coords=dbz.coords,
        attrs={
            "units": "mm/h",
            "long_name": "Rainfall Rate (Z-ZDR)",
            "method": f"R = {a} * Z^{b} * 10^({c} * ZDR)",
        },
    )


# ---------------------------------------------------------------------------
# Attenuation correction
# ---------------------------------------------------------------------------

def correct_attenuation_phidp(
    dbz: xr.DataArray,
    phidp: xr.DataArray,
    *,
    alpha: float = 0.08,
) -> xr.DataArray:
    """Apply simple path-integrated attenuation (PIA) correction using PhiDP.

    PIA = alpha * PhiDP. The corrected reflectivity is Z_corr = Z + PIA.

    Parameters
    ----------
    dbz : xr.DataArray
        Reflectivity in dBZ.
    phidp : xr.DataArray
        Differential phase in degrees.
    alpha : float
        Specific attenuation coefficient (dB per degree of PhiDP).

    Returns
    -------
    xr.DataArray
        Attenuation-corrected reflectivity in dBZ.
    """
    phi_vals = phidp.values.astype(np.float64)
    z_vals = dbz.values.astype(np.float64)

    # Compute cumulative PIA along range from PhiDP
    # Use PhiDP directly (it's already cumulative differential phase)
    pia = alpha * phi_vals
    pia = np.clip(pia, 0.0, None)  # PIA should not be negative

    z_corrected = z_vals + pia
    combined_mask = np.isnan(dbz.values) | np.isnan(phidp.values)
    z_corrected = np.where(combined_mask, np.nan, z_corrected)

    return xr.DataArray(
        z_corrected.astype(np.float32),
        dims=dbz.dims,
        coords=dbz.coords,
        attrs={
            "units": "dBZ",
            "long_name": "Attenuation-Corrected Reflectivity",
            "correction_method": f"PIA = {alpha} * PhiDP",
        },
    )


# ---------------------------------------------------------------------------
# Hydrometeor classification (fuzzy logic)
# ---------------------------------------------------------------------------

# Membership-function centres and widths for each class / variable pair.
# Format: {class_name: {variable: (centre, width)}}
# Simplified trapezoidal membership functions approximated as Gaussians.
_HID_PARAMS: dict[str, dict[str, tuple[float, float]]] = {
    "drizzle":  {"DBZH": (15.0, 10.0), "ZDR": (0.3, 0.4), "KDP": (0.0, 0.1), "RHOHV": (0.99, 0.02)},
    "rain":     {"DBZH": (35.0, 12.0), "ZDR": (1.5, 1.0), "KDP": (0.5, 0.5), "RHOHV": (0.98, 0.02)},
    "wet_snow": {"DBZH": (30.0, 8.0),  "ZDR": (1.0, 0.8), "KDP": (0.1, 0.2), "RHOHV": (0.90, 0.05)},
    "dry_snow": {"DBZH": (20.0, 10.0), "ZDR": (0.3, 0.5), "KDP": (0.0, 0.1), "RHOHV": (0.98, 0.03)},
    "graupel":  {"DBZH": (42.0, 8.0),  "ZDR": (0.2, 0.5), "KDP": (0.2, 0.3), "RHOHV": (0.95, 0.04)},
    "hail":     {"DBZH": (55.0, 8.0),  "ZDR": (-0.5, 1.0), "KDP": (0.0, 0.5), "RHOHV": (0.90, 0.06)},
}

_HID_CLASS_NAMES = list(_HID_PARAMS.keys())
_HID_CLASS_IDS = {name: idx for idx, name in enumerate(_HID_CLASS_NAMES)}


def classify_hydrometeors(
    dbz: xr.DataArray,
    zdr: xr.DataArray,
    kdp: xr.DataArray,
    rhohv: xr.DataArray,
    *,
    weights: dict[str, float] | None = None,
) -> xr.DataArray:
    """Classify hydrometeors using fuzzy-logic membership functions.

    Categories: drizzle (0), rain (1), wet_snow (2), dry_snow (3),
    graupel (4), hail (5). Returns -1 where any input is NaN.

    Parameters
    ----------
    dbz, zdr, kdp, rhohv : xr.DataArray
        Polarimetric variables, all same shape.
    weights : dict, optional
        Per-variable weights (keys: DBZH, ZDR, KDP, RHOHV). Default: equal.

    Returns
    -------
    xr.DataArray
        Integer class index, shape matching inputs.
    """
    if weights is None:
        weights = {"DBZH": 1.0, "ZDR": 1.0, "KDP": 1.0, "RHOHV": 1.0}

    shape = dbz.shape
    n_classes = len(_HID_CLASS_NAMES)

    # Stack inputs
    inputs = {
        "DBZH": dbz.values.astype(np.float64),
        "ZDR": zdr.values.astype(np.float64),
        "KDP": kdp.values.astype(np.float64),
        "RHOHV": rhohv.values.astype(np.float64),
    }

    # NaN mask: any input NaN -> classification invalid
    nan_mask = np.zeros(shape, dtype=bool)
    for v in inputs.values():
        nan_mask |= np.isnan(v)

    # Compute aggregated membership for each class
    scores = np.zeros((*shape, n_classes), dtype=np.float64)
    for cls_idx, cls_name in enumerate(_HID_CLASS_NAMES):
        for var_name, (centre, width) in _HID_PARAMS[cls_name].items():
            w = weights.get(var_name, 1.0)
            val = inputs[var_name]
            # Gaussian membership
            membership = np.exp(-0.5 * ((val - centre) / max(width, 1e-9)) ** 2)
            scores[..., cls_idx] += w * membership

    # Classify as the argmax class
    classification = np.argmax(scores, axis=-1).astype(np.int8)
    classification[nan_mask] = -1

    return xr.DataArray(
        classification,
        dims=dbz.dims,
        coords=dbz.coords,
        attrs={
            "units": "1",
            "long_name": "Hydrometeor Classification",
            "class_names": _HID_CLASS_NAMES,
            "flag_values": list(range(n_classes)),
            "flag_meanings": " ".join(_HID_CLASS_NAMES),
        },
    )


# ---------------------------------------------------------------------------
# Echo classification (clutter vs meteorological)
# ---------------------------------------------------------------------------

def classify_echo(
    dbz: xr.DataArray,
    zdr: xr.DataArray | None = None,
    phidp: xr.DataArray | None = None,
    rhohv: xr.DataArray | None = None,
    *,
    texture_window: int = 5,
    zdr_texture_thresh: float = 2.0,
    phidp_texture_thresh: float = 25.0,
    rhohv_thresh: float = 0.7,
) -> xr.DataArray:
    """Classify radar echoes as meteorological or clutter.

    Uses texture (local standard deviation) of ZDR and PhiDP, plus
    a threshold on RhoHV, to flag clutter. Points with high texture or
    low RhoHV are classified as clutter.

    Result values: 0 = no echo, 1 = meteorological, 2 = clutter.

    Parameters
    ----------
    dbz : xr.DataArray
        Reflectivity in dBZ.
    zdr, phidp, rhohv : xr.DataArray, optional
        Polarimetric variables. If absent, that test is skipped.
    texture_window : int
        Window size for texture (local std dev) computation.
    zdr_texture_thresh : float
        ZDR texture threshold above which echo is clutter.
    phidp_texture_thresh : float
        PhiDP texture threshold above which echo is clutter.
    rhohv_thresh : float
        RhoHV threshold below which echo is clutter.

    Returns
    -------
    xr.DataArray
        Echo classification (0=no echo, 1=meteorological, 2=clutter).
    """
    z = dbz.values.astype(np.float64)
    has_echo = ~np.isnan(z)
    is_clutter = np.zeros(z.shape, dtype=bool)

    # Helper: compute texture as local standard deviation along range
    def _texture(arr: np.ndarray) -> np.ndarray:
        """Rolling std-dev along the last (range) axis."""
        w = max(texture_window, 3)
        half = w // 2
        padded = np.pad(arr, ((0, 0), (half, half)), mode="reflect")
        # Compute rolling std via the variance formula
        out = np.zeros_like(arr)
        for i in range(arr.shape[-1]):
            window = padded[:, i : i + w]
            out[:, i] = np.nanstd(window, axis=-1)
        return out

    if zdr is not None:
        tex_zdr = _texture(zdr.values.astype(np.float64))
        is_clutter |= (tex_zdr > zdr_texture_thresh) & has_echo

    if phidp is not None:
        tex_phidp = _texture(phidp.values.astype(np.float64))
        is_clutter |= (tex_phidp > phidp_texture_thresh) & has_echo

    if rhohv is not None:
        rho = rhohv.values.astype(np.float64)
        is_clutter |= (rho < rhohv_thresh) & has_echo

    # Build classification array
    result = np.zeros(z.shape, dtype=np.int8)
    result[has_echo & ~is_clutter] = 1  # meteorological
    result[has_echo & is_clutter] = 2   # clutter

    return xr.DataArray(
        result,
        dims=dbz.dims,
        coords=dbz.coords,
        attrs={
            "units": "1",
            "long_name": "Echo Classification",
            "flag_values": [0, 1, 2],
            "flag_meanings": "no_echo meteorological clutter",
        },
    )


# ---------------------------------------------------------------------------
# Registry: name -> (function, required_variables, parameter_spec)
# ---------------------------------------------------------------------------

RETRIEVAL_REGISTRY: dict[str, dict[str, Any]] = {
    "kdp_estimation": {
        "function": estimate_kdp,
        "required_variables": ["PHIDP"],
        "params": {
            "window_len": {"type": "int", "default": 21, "label": "Smoothing Window (gates)"},
        },
        "description": "Estimate KDP from PhiDP using finite-difference method",
    },
    "rain_rate_z": {
        "function": rain_rate_z,
        "required_variables": ["DBZH"],
        "params": {
            "a": {"type": "float", "default": 200.0, "label": "Z-R coefficient a"},
            "b": {"type": "float", "default": 1.6, "label": "Z-R exponent b"},
        },
        "description": "Rainfall rate from Z using Marshall-Palmer Z=aR^b",
    },
    "rain_rate_kdp": {
        "function": rain_rate_kdp,
        "required_variables": ["KDP"],
        "params": {
            "a": {"type": "float", "default": 44.0, "label": "R-KDP coefficient a"},
            "b": {"type": "float", "default": 0.822, "label": "R-KDP exponent b"},
        },
        "description": "Rainfall rate from KDP",
    },
    "rain_rate_z_zdr": {
        "function": rain_rate_z_zdr,
        "required_variables": ["DBZH", "ZDR"],
        "params": {
            "a": {"type": "float", "default": 6.7e-3, "label": "Coefficient a"},
            "b": {"type": "float", "default": 0.927, "label": "Exponent b"},
            "c": {"type": "float", "default": -3.43, "label": "ZDR coefficient c"},
        },
        "description": "Rainfall rate from Z and ZDR",
    },
    "attenuation_correction": {
        "function": correct_attenuation_phidp,
        "required_variables": ["DBZH", "PHIDP"],
        "params": {
            "alpha": {"type": "float", "default": 0.08, "label": "Specific attenuation (dB/deg)"},
        },
        "description": "Attenuation correction using PhiDP (PIA method)",
    },
    "hydrometeor_classification": {
        "function": classify_hydrometeors,
        "required_variables": ["DBZH", "ZDR", "KDP", "RHOHV"],
        "params": {},
        "description": "Fuzzy-logic hydrometeor classification (6 classes)",
    },
    "echo_classification": {
        "function": classify_echo,
        "required_variables": ["DBZH"],
        "params": {
            "texture_window": {"type": "int", "default": 5, "label": "Texture window (gates)"},
            "zdr_texture_thresh": {"type": "float", "default": 2.0, "label": "ZDR texture threshold"},
            "phidp_texture_thresh": {"type": "float", "default": 25.0, "label": "PhiDP texture threshold"},
            "rhohv_thresh": {"type": "float", "default": 0.7, "label": "RhoHV threshold"},
        },
        "description": "Echo classification (clutter vs meteorological)",
    },
}


def run_retrieval(
    datatree: xr.DataTree,
    name: str,
    sweep: int = 0,
    params: dict[str, Any] | None = None,
) -> xr.DataArray:
    """Run a named retrieval on a specific sweep of a DataTree.

    Parameters
    ----------
    datatree : xr.DataTree
        Radar DataTree with sweep_N children.
    name : str
        Retrieval name from RETRIEVAL_REGISTRY.
    sweep : int
        Sweep index.
    params : dict, optional
        Override default parameters.

    Returns
    -------
    xr.DataArray
        The retrieval result.

    Raises
    ------
    KeyError
        If the retrieval name is not found.
    ValueError
        If required variables are missing.
    """
    if name not in RETRIEVAL_REGISTRY:
        raise KeyError(f"Unknown retrieval: {name}. Available: {list(RETRIEVAL_REGISTRY.keys())}")

    entry = RETRIEVAL_REGISTRY[name]
    func = entry["function"]
    required_vars = entry["required_variables"]

    # Get sweep dataset
    sweep_name = f"sweep_{sweep}"
    sweep_nodes = sorted(
        [n for n in datatree.children if n.startswith("sweep_")],
    )
    if sweep >= len(sweep_nodes):
        raise IndexError(f"Sweep {sweep} out of range (0..{len(sweep_nodes) - 1})")

    ds = datatree[sweep_nodes[sweep]].to_dataset()

    # Flexible variable name matching (e.g. DBZH, DBZ, reflectivity, Z)
    var_aliases: dict[str, list[str]] = {
        "DBZH": ["DBZH", "DBZ", "reflectivity", "Z", "corrected_reflectivity_horizontal"],
        "ZDR": ["ZDR", "differential_reflectivity", "Zdr"],
        "PHIDP": ["PHIDP", "differential_phase", "PhiDP", "UPHIDP"],
        "KDP": ["KDP", "specific_differential_phase"],
        "RHOHV": ["RHOHV", "cross_correlation_ratio", "RhoHV", "CCORH"],
        "VRADH": ["VRADH", "velocity", "V", "radial_velocity"],
    }

    def _find_var(canonical: str) -> xr.DataArray | None:
        aliases = var_aliases.get(canonical, [canonical])
        for alias in aliases:
            if alias in ds.data_vars:
                return ds[alias]
        return None

    # Collect required data arrays
    data_args: list[xr.DataArray] = []
    for var_name in required_vars:
        da = _find_var(var_name)
        if da is None:
            available = sorted(str(v) for v in ds.data_vars if ds[v].ndim == 2)
            raise ValueError(
                f"Retrieval '{name}' requires '{var_name}' but it was not found. "
                f"Available 2D variables: {available}"
            )
        data_args.append(da)

    # Build keyword arguments from params
    merged_params: dict[str, Any] = {}
    for pname, pspec in entry["params"].items():
        merged_params[pname] = pspec["default"]
    if params:
        for k, v in params.items():
            if k in merged_params:
                merged_params[k] = v

    # Special handling for echo_classification: pass optional polvar arrays
    if name == "echo_classification":
        zdr_da = _find_var("ZDR")
        phidp_da = _find_var("PHIDP")
        rhohv_da = _find_var("RHOHV")
        return func(data_args[0], zdr=zdr_da, phidp=phidp_da, rhohv=rhohv_da, **merged_params)

    return func(*data_args, **merged_params)
