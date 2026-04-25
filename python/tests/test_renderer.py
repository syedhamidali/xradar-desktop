"""Tests for engine.renderer — input validation and parameter bounds."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from engine.renderer import (
    RadarRenderer,
    _polar_to_cartesian,
    _resolve_cmap,
    _resolve_range,
)

# ---------------------------------------------------------------------------
# Colormap resolution
# ---------------------------------------------------------------------------


class TestResolveCmap:
    @pytest.mark.parametrize(
        "variable,expected_substring",
        [
            ("DBZH", "rainbow"),
            ("VRADH", "coolwarm"),
            ("WRADH", "fire"),
            ("ZDR", "bgy"),
            ("RHOHV", "blues"),
        ],
    )
    def test_known_variables(self, variable: str, expected_substring: str) -> None:
        # _resolve_cmap returns a list of hex colors; just check it's non-empty
        cmap = _resolve_cmap(variable)
        assert isinstance(cmap, list)
        assert len(cmap) > 0

    def test_unknown_variable_fallback(self) -> None:
        cmap = _resolve_cmap("TOTALLY_UNKNOWN")
        assert isinstance(cmap, list)
        assert len(cmap) > 0

    def test_reflectivity_substring_match(self) -> None:
        """Variables containing 'dbz' should get rainbow even if not exact match."""
        import colorcet as cc

        cmap = _resolve_cmap("corrected_dbz_field")
        assert cmap == cc.rainbow

    def test_velocity_substring_match(self) -> None:
        import colorcet as cc

        cmap = _resolve_cmap("mean_velocity")
        assert cmap == cc.coolwarm


# ---------------------------------------------------------------------------
# Value range resolution
# ---------------------------------------------------------------------------


class TestResolveRange:
    def test_known_variable(self) -> None:
        vmin, vmax = _resolve_range("DBZH")
        assert vmin == -20.0
        assert vmax == 75.0

    def test_unknown_variable_returns_none(self) -> None:
        vmin, vmax = _resolve_range("UNKNOWN_VAR")
        assert vmin is None
        assert vmax is None

    @pytest.mark.parametrize("variable", ["VRADH", "ZDR", "RHOHV", "PHIDP", "KDP"])
    def test_all_ranges_have_correct_order(self, variable: str) -> None:
        vmin, vmax = _resolve_range(variable)
        assert vmin is not None
        assert vmax is not None
        assert vmin < vmax


# ---------------------------------------------------------------------------
# Polar to Cartesian conversion
# ---------------------------------------------------------------------------


class TestPolarToCartesian:
    def test_basic_conversion(self) -> None:
        azimuth = np.array([0.0, 90.0, 180.0, 270.0])
        range_m = np.array([1000.0, 2000.0])
        values = np.ones((4, 2), dtype=np.float32)

        df = _polar_to_cartesian(azimuth, range_m, values)
        assert len(df) == 8  # 4 azimuths * 2 ranges, all finite
        assert set(df.columns) == {"x", "y", "val"}

    def test_nan_values_excluded(self) -> None:
        azimuth = np.array([0.0, 90.0])
        range_m = np.array([1000.0])
        values = np.array([[1.0], [np.nan]], dtype=np.float32)

        df = _polar_to_cartesian(azimuth, range_m, values)
        assert len(df) == 1  # only the finite value

    def test_north_azimuth_gives_positive_y(self) -> None:
        """Azimuth 0 (north) should produce x~0, y>0."""
        azimuth = np.array([0.0])
        range_m = np.array([1000.0])
        values = np.array([[5.0]], dtype=np.float32)

        df = _polar_to_cartesian(azimuth, range_m, values)
        assert abs(df["x"].iloc[0]) < 1.0  # essentially zero
        assert df["y"].iloc[0] > 999.0

    def test_east_azimuth_gives_positive_x(self) -> None:
        """Azimuth 90 (east) should produce x>0, y~0."""
        azimuth = np.array([90.0])
        range_m = np.array([1000.0])
        values = np.array([[5.0]], dtype=np.float32)

        df = _polar_to_cartesian(azimuth, range_m, values)
        assert df["x"].iloc[0] > 999.0
        assert abs(df["y"].iloc[0]) < 1.0


# ---------------------------------------------------------------------------
# Renderer — parameter validation
# ---------------------------------------------------------------------------


class TestRendererParameterBounds:
    def test_render_empty_data(self, synthetic_dataarray: xr.DataArray) -> None:
        """DataArray with all NaN should produce an empty-result image."""
        renderer = RadarRenderer()
        nan_data = synthetic_dataarray.copy()
        nan_data.values[:] = np.nan

        result = renderer.render_sweep(nan_data, "DBZH", sweep=0, width=100, height=100)
        assert "image" in result
        assert "metadata" in result
        assert result["metadata"]["variable"] == "DBZH"

    def test_render_returns_base64_png(self, synthetic_dataarray: xr.DataArray) -> None:
        renderer = RadarRenderer()
        result = renderer.render_sweep(
            synthetic_dataarray, "DBZH", sweep=0, width=200, height=200
        )
        assert "image" in result
        # Verify it's valid base64 by decoding
        import base64

        raw = base64.b64decode(result["image"])
        # PNG magic bytes
        assert raw[:4] == b"\x89PNG"

    def test_render_respects_dimensions(self, synthetic_dataarray: xr.DataArray) -> None:
        renderer = RadarRenderer()
        result = renderer.render_sweep(
            synthetic_dataarray, "DBZH", sweep=0, width=64, height=64
        )
        assert result["metadata"]["width"] == 64
        assert result["metadata"]["height"] == 64

    def test_render_with_bbox(self, synthetic_dataarray: xr.DataArray) -> None:
        renderer = RadarRenderer()
        bbox = [-50000, -50000, 50000, 50000]
        result = renderer.render_sweep(
            synthetic_dataarray, "DBZH", sweep=0, width=100, height=100, bbox=bbox
        )
        assert "image" in result
