"""Tests for engine.exporter — format selection, path validation, error handling."""

from __future__ import annotations

from pathlib import Path

import pytest
import xarray as xr
from engine.exporter import RadarExporter

# ---------------------------------------------------------------------------
# Format dispatch
# ---------------------------------------------------------------------------


class TestFormatSelection:
    def test_unsupported_format_raises(self, synthetic_datatree: xr.DataTree) -> None:
        exporter = RadarExporter()
        with pytest.raises(ValueError, match="Unsupported export format"):
            exporter.export(synthetic_datatree, fmt="bmp")

    @pytest.mark.parametrize("fmt", ["png", "PNG", "  png  ", "Png"])
    def test_format_normalization(
        self, synthetic_datatree: xr.DataTree, tmp_output_file: Path, fmt: str
    ) -> None:
        """Format strings should be lowered and stripped before dispatch."""
        exporter = RadarExporter()
        result = exporter.export(
            synthetic_datatree,
            fmt=fmt,
            output_path=str(tmp_output_file),
            variable="DBZH",
            sweep=0,
        )
        assert Path(result).exists()

    def test_geotiff_not_implemented(self, synthetic_datatree: xr.DataTree) -> None:
        exporter = RadarExporter()
        with pytest.raises(NotImplementedError, match="GeoTIFF"):
            exporter.export(synthetic_datatree, fmt="geotiff")


# ---------------------------------------------------------------------------
# Image export (PNG)
# ---------------------------------------------------------------------------


class TestImageExport:
    def test_png_creates_file(self, synthetic_datatree: xr.DataTree, tmp_output_file: Path) -> None:
        exporter = RadarExporter()
        result = exporter.export(
            synthetic_datatree,
            fmt="png",
            output_path=str(tmp_output_file),
            variable="DBZH",
            sweep=0,
        )
        assert Path(result).exists()
        assert Path(result).stat().st_size > 0

    def test_png_auto_selects_variable(
        self, synthetic_datatree: xr.DataTree, tmp_output_file: Path
    ) -> None:
        """When variable is None, exporter should pick the first data var."""
        exporter = RadarExporter()
        result = exporter.export(
            synthetic_datatree,
            fmt="png",
            output_path=str(tmp_output_file),
            variable=None,
            sweep=0,
        )
        assert Path(result).exists()

    def test_png_invalid_sweep_raises(
        self, synthetic_datatree: xr.DataTree, tmp_output_file: Path
    ) -> None:
        exporter = RadarExporter()
        with pytest.raises(IndexError, match="out of range"):
            exporter.export(
                synthetic_datatree,
                fmt="png",
                output_path=str(tmp_output_file),
                variable="DBZH",
                sweep=999,
            )

    def test_png_invalid_variable_raises(
        self, synthetic_datatree: xr.DataTree, tmp_output_file: Path
    ) -> None:
        exporter = RadarExporter()
        with pytest.raises(KeyError, match="not found"):
            exporter.export(
                synthetic_datatree,
                fmt="png",
                output_path=str(tmp_output_file),
                variable="NONEXISTENT",
                sweep=0,
            )

    def test_png_generates_temp_path_when_none(self, synthetic_datatree: xr.DataTree) -> None:
        exporter = RadarExporter()
        result = exporter.export(
            synthetic_datatree,
            fmt="png",
            output_path=None,
            variable="DBZH",
            sweep=0,
        )
        assert Path(result).exists()
        # Clean up
        Path(result).unlink(missing_ok=True)

    def test_progress_callback_called(
        self, synthetic_datatree: xr.DataTree, tmp_output_file: Path
    ) -> None:
        exporter = RadarExporter()
        calls: list[tuple[int, str]] = []

        def track(percent: int, message: str) -> None:
            calls.append((percent, message))

        exporter.export(
            synthetic_datatree,
            fmt="png",
            output_path=str(tmp_output_file),
            variable="DBZH",
            sweep=0,
            progress=track,
        )
        assert len(calls) >= 2
        # Last call should be 100%
        assert calls[-1][0] == 100


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_output_path_is_resolved(self, synthetic_datatree: xr.DataTree, tmp_path: Path) -> None:
        exporter = RadarExporter()
        relative_ish = str(tmp_path / "subdir" / ".." / "out.png")
        # Ensure parent exists
        (tmp_path / "subdir").mkdir(exist_ok=True)
        result = exporter.export(
            synthetic_datatree,
            fmt="png",
            output_path=relative_ish,
            variable="DBZH",
            sweep=0,
        )
        resolved = Path(result)
        assert resolved.is_absolute()
        assert ".." not in str(resolved)
