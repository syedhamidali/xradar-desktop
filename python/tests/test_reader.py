"""Tests for engine.reader.RadarReader."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from engine.reader import RadarReader, _guess_formats_by_extension

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_synthetic_datatree() -> xr.DataTree:
    """Build a minimal synthetic radar DataTree that mimics xradar output."""
    n_azimuth = 360
    n_range = 500

    azimuth = np.linspace(0, 359, n_azimuth)
    range_m = np.linspace(0, 150_000, n_range)

    sweeps: dict[str, xr.Dataset] = {}
    for i, elev in enumerate([0.5, 1.5, 2.4]):
        ds = xr.Dataset(
            {
                "DBZH": (
                    ["azimuth", "range"],
                    np.random.default_rng(i)
                    .uniform(-20, 75, (n_azimuth, n_range))
                    .astype(np.float32),
                ),
                "VRADH": (
                    ["azimuth", "range"],
                    np.random.default_rng(i + 100)
                    .uniform(-30, 30, (n_azimuth, n_range))
                    .astype(np.float32),
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
    dtree = xr.DataTree.from_dict({"/": root_ds, **{f"/{k}": v for k, v in sweeps.items()}})
    return dtree


# ---------------------------------------------------------------------------
# Tests: instantiation
# ---------------------------------------------------------------------------


class TestRadarReaderInstantiation:
    def test_create_reader(self) -> None:
        reader = RadarReader()
        assert reader.datatree is None

    def test_get_schema_without_open_raises(self) -> None:
        reader = RadarReader()
        with pytest.raises(RuntimeError, match="No file is currently open"):
            reader.get_schema()

    def test_get_sweep_data_without_open_raises(self) -> None:
        reader = RadarReader()
        with pytest.raises(RuntimeError, match="No file is currently open"):
            reader.get_sweep_data(0, "DBZH")


# ---------------------------------------------------------------------------
# Tests: schema extraction from synthetic data
# ---------------------------------------------------------------------------


class TestSchemaExtraction:
    @pytest.fixture()
    def reader_with_data(self) -> RadarReader:
        reader = RadarReader()
        reader._dtree = _make_synthetic_datatree()
        reader._path = "/fake/path.nc"
        reader._format = "synthetic"
        return reader

    def test_schema_has_required_keys(self, reader_with_data: RadarReader) -> None:
        schema = reader_with_data.get_schema()
        assert "variables" in schema
        assert "dimensions" in schema
        assert "attributes" in schema
        assert "sweeps" in schema

    def test_schema_variables(self, reader_with_data: RadarReader) -> None:
        schema = reader_with_data.get_schema()
        assert "DBZH" in schema["variables"]
        assert "VRADH" in schema["variables"]

    def test_schema_dimensions(self, reader_with_data: RadarReader) -> None:
        schema = reader_with_data.get_schema()
        assert "azimuth" in schema["dimensions"]
        assert "range" in schema["dimensions"]
        assert schema["dimensions"]["azimuth"] == 360
        assert schema["dimensions"]["range"] == 500

    def test_schema_sweeps(self, reader_with_data: RadarReader) -> None:
        schema = reader_with_data.get_schema()
        assert len(schema["sweeps"]) == 3
        elevations = [s["elevation"] for s in schema["sweeps"]]
        assert elevations == pytest.approx([0.5, 1.5, 2.4], abs=0.01)

    def test_schema_attributes(self, reader_with_data: RadarReader) -> None:
        schema = reader_with_data.get_schema()
        assert schema["attributes"].get("instrument_name") == "SYNTHETIC"


# ---------------------------------------------------------------------------
# Tests: sweep data access
# ---------------------------------------------------------------------------


class TestSweepDataAccess:
    @pytest.fixture()
    def reader_with_data(self) -> RadarReader:
        reader = RadarReader()
        reader._dtree = _make_synthetic_datatree()
        reader._path = "/fake/path.nc"
        reader._format = "synthetic"
        return reader

    def test_get_sweep_data_returns_dataarray(self, reader_with_data: RadarReader) -> None:
        da = reader_with_data.get_sweep_data(0, "DBZH")
        assert isinstance(da, xr.DataArray)
        assert da.shape == (360, 500)

    def test_get_sweep_data_invalid_sweep(self, reader_with_data: RadarReader) -> None:
        with pytest.raises(IndexError, match="out of range"):
            reader_with_data.get_sweep_data(99, "DBZH")

    def test_get_sweep_data_invalid_variable(self, reader_with_data: RadarReader) -> None:
        with pytest.raises(KeyError, match="not in sweep"):
            reader_with_data.get_sweep_data(0, "NONEXISTENT")

    def test_different_sweeps_return_different_data(self, reader_with_data: RadarReader) -> None:
        da0 = reader_with_data.get_sweep_data(0, "DBZH")
        da1 = reader_with_data.get_sweep_data(1, "DBZH")
        # Different RNG seeds should produce different data
        assert not np.array_equal(da0.values, da1.values)


# ---------------------------------------------------------------------------
# Tests: format guessing
# ---------------------------------------------------------------------------


class TestFormatGuessing:
    def test_nexrad_gz(self) -> None:
        formats = _guess_formats_by_extension("KTLX20200101_000000_V06.gz")
        assert formats[0] == "nexrad_level2"

    def test_cfradial_nc(self) -> None:
        formats = _guess_formats_by_extension("radar_data.nc")
        assert formats[0] == "cfradial1"

    def test_odim_h5(self) -> None:
        formats = _guess_formats_by_extension("radar.h5")
        assert formats[0] == "odim"

    def test_no_extension_tries_nexrad_first(self) -> None:
        formats = _guess_formats_by_extension("KTLX20200101_000000_V06")
        assert formats[0] == "nexrad_level2"

    def test_unknown_extension_tries_all(self) -> None:
        formats = _guess_formats_by_extension("radar.xyz")
        # Should include all formats as fallbacks
        assert len(formats) >= 6

    def test_open_file_nonexistent(self) -> None:
        reader = RadarReader()
        with pytest.raises(FileNotFoundError):
            reader.open_file("/nonexistent/file.nc")
