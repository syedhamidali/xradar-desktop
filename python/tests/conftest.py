"""Shared pytest fixtures for xradar-desktop engine tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

# ---------------------------------------------------------------------------
# Synthetic radar data
# ---------------------------------------------------------------------------

N_AZIMUTH = 360
N_RANGE = 500
ELEVATIONS = [0.5, 1.5, 2.4]


@pytest.fixture()
def synthetic_datatree() -> xr.DataTree:
    """Build a minimal synthetic radar DataTree that mimics xradar output."""
    return make_synthetic_datatree()


def make_synthetic_datatree(
    n_azimuth: int = N_AZIMUTH,
    n_range: int = N_RANGE,
    elevations: list[float] | None = None,
) -> xr.DataTree:
    """Reusable helper — not a fixture so tests can call it directly."""
    if elevations is None:
        elevations = list(ELEVATIONS)

    azimuth = np.linspace(0, 359, n_azimuth)
    range_m = np.linspace(0, 150_000, n_range)

    sweeps: dict[str, xr.Dataset] = {}
    for i, elev in enumerate(elevations):
        rng = np.random.default_rng(i)
        ds = xr.Dataset(
            {
                "DBZH": (
                    ["azimuth", "range"],
                    rng.uniform(-20, 75, (n_azimuth, n_range)).astype(np.float32),
                ),
                "VRADH": (
                    ["azimuth", "range"],
                    rng.uniform(-30, 30, (n_azimuth, n_range)).astype(np.float32),
                ),
            },
            coords={
                "azimuth": azimuth,
                "range": range_m,
                "elevation": ("azimuth", np.full(n_azimuth, elev, dtype=np.float32)),
            },
            attrs={"sweep_fixed_angle": elev},
        )
        sweeps[f"sweep_{i}"] = ds

    root_ds = xr.Dataset(attrs={"instrument_name": "SYNTHETIC"})
    return xr.DataTree.from_dict({"/": root_ds, **{f"/{k}": v for k, v in sweeps.items()}})


@pytest.fixture()
def synthetic_dataarray() -> xr.DataArray:
    """A single-sweep DataArray suitable for renderer / arrow-bridge tests."""
    rng = np.random.default_rng(42)
    n_az, n_rng = 360, 500
    values = rng.uniform(-20, 75, (n_az, n_rng)).astype(np.float32)
    return xr.DataArray(
        values,
        dims=["azimuth", "range"],
        coords={
            "azimuth": np.linspace(0, 359, n_az),
            "range": np.linspace(0, 150_000, n_rng),
        },
        name="DBZH",
    )


# ---------------------------------------------------------------------------
# Temporary directories
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    """Provide a clean temporary directory (pytest built-in, re-exported for clarity)."""
    return tmp_path


@pytest.fixture()
def tmp_output_file(tmp_path: Path) -> Path:
    """Provide a temporary file path for export tests (file does not exist yet)."""
    return tmp_path / "output.png"
