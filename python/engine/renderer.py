"""Radar sweep renderer — fast NumPy-based PPI rasterization."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import colorcet as cc
import numpy as np
import xarray as xr
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# --- Rendering constraints ---
MIN_DIMENSION = 1
MAX_DIMENSION = 10000
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 800
DEFAULT_BACKGROUND = "black"
PNG_FORMAT = "PNG"

_PALETTES: dict[str, list[str]] = {
    "rainbow": cc.rainbow,
    "coolwarm": cc.coolwarm,
    "fire": cc.fire,
    "blues": cc.blues,
    "bgy": cc.bgy,
    "kr": cc.kr,
}

_VARIABLE_CMAPS: dict[str, str] = {
    "DBZH": "rainbow",
    "DBZ": "rainbow",
    "reflectivity": "rainbow",
    "corrected_reflectivity": "rainbow",
    "total_power": "rainbow",
    "VRADH": "coolwarm",
    "VEL": "coolwarm",
    "velocity": "coolwarm",
    "corrected_velocity": "coolwarm",
    "WRADH": "fire",
    "WIDTH": "fire",
    "spectrum_width": "fire",
    "ZDR": "bgy",
    "differential_reflectivity": "bgy",
    "RHOHV": "blues",
    "cross_correlation_ratio": "blues",
    "PHIDP": "fire",
    "differential_phase": "fire",
    "KDP": "bgy",
    "specific_differential_phase": "bgy",
}

_VARIABLE_RANGES: dict[str, tuple[float, float]] = {
    "DBZH": (-20.0, 75.0),
    "DBZ": (-20.0, 75.0),
    "reflectivity": (-20.0, 75.0),
    "corrected_reflectivity": (-20.0, 75.0),
    "total_power": (-20.0, 75.0),
    "VRADH": (-30.0, 30.0),
    "VEL": (-30.0, 30.0),
    "velocity": (-30.0, 30.0),
    "corrected_velocity": (-30.0, 30.0),
    "WRADH": (0.0, 10.0),
    "WIDTH": (0.0, 10.0),
    "spectrum_width": (0.0, 10.0),
    "ZDR": (-1.0, 5.0),
    "differential_reflectivity": (-1.0, 5.0),
    "RHOHV": (0.0, 1.05),
    "cross_correlation_ratio": (0.0, 1.05),
    "PHIDP": (0.0, 180.0),
    "differential_phase": (0.0, 180.0),
    "KDP": (-2.0, 6.0),
    "specific_differential_phase": (-2.0, 6.0),
}


# ---------------------------------------------------------------------------
# Colorbar helpers
# ---------------------------------------------------------------------------

# Semi-transparent dark background for the colorbar strip (RGBA).
_CB_BG: tuple[int, int, int, int] = (15, 15, 15, 200)
# Width of the color-gradient swatch in pixels.
_CB_SWATCH_W: int = 18
# Padding on all sides (px).
_CB_PAD: int = 5
# Total colorbar strip width: swatch + left-pad + right-pad + ~18 px for labels.
_CB_STRIP_W: int = 45


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert a CSS hex color string (e.g. '#ab12ef') to an (R, G, B) tuple."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _cb_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Return a PIL font at *size* points, falling back to the built-in bitmap."""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNSText.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "DejaVuSans.ttf",
        "arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _fmt_tick(v: float) -> str:
    """Format a tick value compactly."""
    if v == int(v) and abs(v) < 1e5:
        return str(int(v))
    return f"{v:.1f}"


def _build_colorbar(
    palette: list[str],
    vmin: float,
    vmax: float,
    variable: str,
    units: str,
    height: int,
) -> Image.Image:
    """
    Build a vertical colorbar strip (RGBA) of size (_CB_STRIP_W × height).

    Layout within the strip:
        [_CB_PAD] [title row ~11 px] [gradient swatch _CB_SWATCH_W wide]
        with tick labels to the right of the swatch.

    Gradient direction: top = vmax color, bottom = vmin color.
    """
    strip_w = _CB_STRIP_W
    img = Image.new("RGBA", (strip_w, height), _CB_BG)
    draw = ImageDraw.Draw(img)

    font_sm = _cb_font(9)
    font_title = _cb_font(9)

    # Vertical regions
    title_h = 11        # px at top for variable / unit label
    bottom_label_h = 11 # px at bottom so the min tick label isn't clipped
    grad_y0 = _CB_PAD + title_h
    grad_y1 = height - _CB_PAD - bottom_label_h
    grad_h = max(grad_y1 - grad_y0, 1)

    swatch_x0 = _CB_PAD
    swatch_x1 = swatch_x0 + _CB_SWATCH_W

    # --- colour gradient ---
    n = len(palette)
    for row in range(grad_h):
        # top row → vmax (palette end), bottom row → vmin (palette start)
        frac = 1.0 - row / grad_h
        idx = min(int(frac * n), n - 1)
        r, g, b = _hex_to_rgb(palette[idx])
        draw.rectangle(
            [(swatch_x0, grad_y0 + row), (swatch_x1 - 1, grad_y0 + row)],
            fill=(r, g, b, 255),
        )

    # --- tick labels ---
    lx = swatch_x1 + 2  # x position for label text

    def _place_label(text: str, row_y: int, color: tuple[int, int, int, int]) -> None:
        draw.text((lx, row_y), text, font=font_sm, fill=color)

    bright = (220, 220, 220, 255)
    mid_col = (180, 180, 180, 255)

    _place_label(_fmt_tick(vmax), grad_y0, bright)
    _place_label(_fmt_tick((vmin + vmax) / 2.0), grad_y0 + grad_h // 2 - 4, mid_col)
    _place_label(_fmt_tick(vmin), grad_y1 - 9, bright)

    # --- title: variable name + units ---
    title = f"{variable}" if not units else f"{variable} ({units})"
    # Truncate to fit the strip width (≤ 7 chars + ellipsis)
    if len(title) > 8:
        title = title[:7] + "…"
    draw.text((_CB_PAD, _CB_PAD), title, font=font_title, fill=(210, 210, 210, 255))

    return img


def _composite_colorbar(
    radar_img: Image.Image,
    palette: list[str],
    vmin: float,
    vmax: float,
    variable: str,
    units: str,
) -> Image.Image:
    """
    Composite a colorbar strip onto the right side of *radar_img*.

    Returns a new RGBA image that is _CB_STRIP_W pixels wider.
    """
    w, h = radar_img.size
    colorbar = _build_colorbar(palette, vmin, vmax, variable, units, h)

    # Create output canvas (radar image + colorbar side-by-side)
    out = Image.new("RGBA", (w + _CB_STRIP_W, h), (0, 0, 0, 255))
    # Paste the radar frame (convert to RGBA so alpha_composite works uniformly)
    out.paste(radar_img.convert("RGBA"), (0, 0))
    # Alpha-composite the colorbar so its semi-transparent background blends
    out.alpha_composite(colorbar, dest=(w, 0))
    return out


def _resolve_cmap(variable: str) -> list[str]:
    name = _VARIABLE_CMAPS.get(variable)
    if name is not None:
        return _PALETTES.get(name, cc.rainbow)
    vlow = variable.lower()
    if any(tok in vlow for tok in ("dbz", "reflectivity", "power")):
        return cc.rainbow
    if any(tok in vlow for tok in ("vel", "vrad")):
        return cc.coolwarm
    return cc.fire


def _resolve_range(variable: str) -> tuple[float | None, float | None]:
    r = _VARIABLE_RANGES.get(variable)
    return r if r is not None else (None, None)


def _polar_to_cartesian(
    azimuth: np.ndarray,
    range_m: np.ndarray,
    values: np.ndarray,
):
    """Convert polar radar gates to finite Cartesian points.

    Coordinates use radar convention: azimuth 0 points north (+y), and
    azimuth 90 points east (+x).
    """
    import pandas as pd

    az = np.asarray(azimuth, dtype=np.float64)
    rng = np.asarray(range_m, dtype=np.float64)
    vals = np.asarray(values)
    if vals.shape != (az.size, rng.size):
        raise ValueError(
            f"values shape {vals.shape} does not match azimuth/range "
            f"({az.size}, {rng.size})"
        )

    az_rad = np.deg2rad(az)[:, None]
    x = np.sin(az_rad) * rng[None, :]
    y = np.cos(az_rad) * rng[None, :]
    finite = np.isfinite(vals)

    return pd.DataFrame(
        {
            "x": x[finite],
            "y": y[finite],
            "val": vals[finite],
        }
    )


def _rasterize_ppi(
    azimuth: np.ndarray,
    range_m: np.ndarray,
    values: np.ndarray,
    width: int,
    height: int,
    x_range: tuple[float, float],
    y_range: tuple[float, float],
) -> np.ndarray:
    """Inverse-map rasterization: for each output pixel, find nearest (az, range) gate.

    This is ~10x faster than datashader and produces a gap-free PPI image.

    Returns a (height, width) float32 array with NaN for missing data.
    """
    x_min, x_max = x_range
    y_min, y_max = y_range

    # Build pixel coordinate grids (data-space metres)
    col = np.linspace(x_min, x_max, width, dtype=np.float32)
    row = np.linspace(y_max, y_min, height, dtype=np.float32)  # y_max at top
    xx, yy = np.meshgrid(col, row)

    # Pixel polar coordinates
    r_pix = np.sqrt(xx * xx + yy * yy)
    az_pix = np.degrees(np.arctan2(xx, yy)) % 360.0

    # Sort azimuths for fast lookup
    sort_idx = np.argsort(azimuth)
    az_sorted = azimuth[sort_idx]

    # Nearest azimuth index (handle wraparound at 0/360)
    az_idx = np.searchsorted(az_sorted, az_pix, side="left")
    az_idx = np.clip(az_idx, 0, len(azimuth) - 1)
    # Also check the previous index for closer match
    az_idx_prev = np.clip(az_idx - 1, 0, len(azimuth) - 1)
    d_cur = np.abs(az_sorted[az_idx] - az_pix)
    d_prev = np.abs(az_sorted[az_idx_prev] - az_pix)
    # Handle 360 wraparound distance
    d_cur = np.minimum(d_cur, 360.0 - d_cur)
    d_prev = np.minimum(d_prev, 360.0 - d_prev)
    use_prev = d_prev < d_cur
    az_idx[use_prev] = az_idx_prev[use_prev]
    az_idx_orig = sort_idx[az_idx]

    # Nearest range index
    rng_idx = np.searchsorted(range_m, r_pix, side="left")
    rng_idx = np.clip(rng_idx, 0, len(range_m) - 1)

    # Mask: only within data annulus
    in_range = (r_pix >= range_m[0]) & (r_pix <= range_m[-1])

    # Build output grid
    grid = np.full((height, width), np.nan, dtype=np.float32)
    grid[in_range] = values[az_idx_orig[in_range], rng_idx[in_range]]

    return grid


def _apply_colormap(
    grid: np.ndarray,
    palette: list[str],
    vmin: float,
    vmax: float,
) -> Image.Image:
    """Apply a colorcet palette to a 2D float grid, returning an RGBA PIL Image.

    NaN values become the background colour (black, fully opaque).
    """
    h, w = grid.shape
    # Pre-compute palette as uint8 array (cached across calls)
    pal_key = id(palette)
    if pal_key not in _PAL_RGB_CACHE:
        _PAL_RGB_CACHE[pal_key] = np.array(
            [[int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)] for c in palette],
            dtype=np.uint8,
        )
    pal_rgb = _PAL_RGB_CACHE[pal_key]
    n_colors = len(pal_rgb)

    # Normalize data to colour index
    span = vmax - vmin
    if span <= 0:
        span = 1.0
    with np.errstate(invalid="ignore"):
        norm = (grid - vmin) / span
        np.clip(norm, 0.0, 1.0, out=norm)
        color_idx = np.nan_to_num(norm * (n_colors - 1), nan=0).astype(np.int32)

    # Build RGBA output
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    finite = np.isfinite(grid)
    rgba[finite, :3] = pal_rgb[color_idx[finite]]
    rgba[finite, 3] = 255
    # Background: black opaque
    rgba[~finite] = (0, 0, 0, 255)

    return Image.fromarray(rgba, "RGBA")


# Module-level palette RGB cache — avoids re-parsing hex strings every render.
_PAL_RGB_CACHE: dict[int, np.ndarray] = {}


class RadarRenderer:
    """Renders radar sweep data into base64-encoded PNG images.

    Uses fast NumPy inverse-mapping rasterization,
    achieving ~10x faster renders (~100ms vs ~1500ms for a typical NEXRAD scan).
    """

    def __init__(self) -> None:
        # LRU-style cache: (variable, sweep, width, height) -> result dict
        self._cache: dict[tuple[str, int, int, int], dict[str, Any]] = {}
        self._max_cache = 50

    def invalidate_cache(self) -> None:
        """Clear the render cache (call when a new file is opened)."""
        self._cache.clear()

    def render_sweep(
        self,
        data: xr.DataArray,
        variable: str,
        sweep: int,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        bbox: list[float] | None = None,
    ) -> dict[str, Any]:
        # Validate numeric parameters
        if not isinstance(width, int) or width < MIN_DIMENSION or width > MAX_DIMENSION:
            raise ValueError(
                f"width must be an integer between {MIN_DIMENSION} and {MAX_DIMENSION}, got {width}"
            )
        if not isinstance(height, int) or height < MIN_DIMENSION or height > MAX_DIMENSION:
            raise ValueError(
                f"height must be an integer between {MIN_DIMENSION}"
                f" and {MAX_DIMENSION}, got {height}"
            )
        if not isinstance(sweep, int) or sweep < 0:
            raise ValueError(f"sweep must be a non-negative integer, got {sweep}")
        if bbox is not None and (
            len(bbox) != 4 or not all(isinstance(v, (int, float)) for v in bbox)
        ):
            raise ValueError("bbox must be a list of exactly 4 numeric values")

        # Check cache (bbox not cached — only default views)
        cache_key = (variable, sweep, width, height)
        if bbox is None and cache_key in self._cache:
            logger.info("Cache hit for %s sweep=%d %dx%d", variable, sweep, width, height)
            return self._cache[cache_key]

        logger.info(
            "Rendering variable=%s sweep=%d size=%dx%d", variable, sweep, width, height
        )

        # Extract unit string from xarray attributes (best-effort).
        units: str = str(data.attrs.get("units", data.attrs.get("unit", "")))

        values = data.values
        azimuth, range_m = self._extract_polar_coords(data)

        if not np.any(np.isfinite(values)):
            return self._empty_result(variable, sweep, width, height)

        vmin, vmax = _resolve_range(variable)

        # Fall back to actual data range when no entry in _VARIABLE_RANGES.
        if vmin is None:
            vmin = float(np.nanmin(values))
        if vmax is None:
            vmax = float(np.nanmax(values))

        if bbox and len(bbox) == 4:
            x_range = (float(bbox[0]), float(bbox[2]))
            y_range = (float(bbox[1]), float(bbox[3]))
        else:
            max_r = float(range_m.max())
            x_range = (-max_r, max_r)
            y_range = (-max_r, max_r)

        # Fast numpy-based PPI rasterization (inverse mapping)
        grid = _rasterize_ppi(azimuth, range_m, values, width, height, x_range, y_range)

        # Apply colormap
        cmap_palette = _resolve_cmap(variable)
        pil_img = _apply_colormap(grid, cmap_palette, vmin, vmax)

        # Overlays
        pil_img = self._draw_overlays(pil_img, x_range, y_range)
        pil_img = _composite_colorbar(pil_img, cmap_palette, vmin, vmax, variable, units)

        # Encode PNG
        buf = io.BytesIO()
        pil_img.save(buf, format=PNG_FORMAT)
        buf.seek(0)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        logger.info("Render complete — %d bytes PNG", len(buf.getvalue()))

        result = {
            "image": b64,
            "metadata": {
                "variable": variable,
                "sweep": sweep,
                "width": width,
                "height": height,
                "units": units,
                "vmin": vmin,
                "vmax": vmax,
                "x_range": [x_range[0] / 1000.0, x_range[1] / 1000.0],
                "y_range": [y_range[0] / 1000.0, y_range[1] / 1000.0],
            },
        }

        # Store in cache
        if bbox is None:
            if len(self._cache) >= self._max_cache:
                oldest = next(iter(self._cache))
                del self._cache[oldest]
            self._cache[cache_key] = result

        return result

    # ------------------------------------------------------------------
    # Overlay helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_ring_interval(max_range_m: float) -> float:
        """Return a ring spacing (metres) that yields 4–5 rings for *max_range_m*."""
        # Candidate spacings in metres (10 km → 500 km)
        candidates = [
            10_000, 20_000, 25_000, 50_000,
            100_000, 150_000, 200_000, 250_000, 500_000,
        ]
        for spacing in candidates:
            n_rings = int(max_range_m / spacing)
            if 4 <= n_rings <= 5:
                return spacing
        # Fallback: just divide into 5 equal rings
        return max_range_m / 5.0

    @staticmethod
    def _draw_overlays(
        pil_img: Image.Image,
        x_range: tuple[float, float],
        y_range: tuple[float, float],
    ) -> Image.Image:
        """Overlay range rings, crosshair, and range labels onto *pil_img*.

        Coordinate system:
            - Data space: x_range / y_range in metres, centre = (0, 0).
            - Pixel space: (0, 0) = top-left corner of the image.
              Datashader places y=y_range[1] at row 0 (top) and
              y=y_range[0] at row height-1 (bottom).
        """
        img = pil_img.convert("RGBA")
        w, h = img.size

        x_min, x_max = x_range
        y_min, y_max = y_range
        data_w = x_max - x_min   # metres spanning full image width
        data_h = y_max - y_min   # metres spanning full image height

        def data_to_pixel(xd: float, yd: float) -> tuple[int, int]:
            """Map data-space (metres) to pixel (col, row)."""
            px = int((xd - x_min) / data_w * w)
            py = int((y_max - yd) / data_h * h)
            return px, py

        def metres_to_px_radius(r_m: float) -> float:
            """Convert a radial distance in metres to pixel radius."""
            # x and y scales are equal when the canvas is square and symmetric,
            # but we compute from x to be safe.
            return r_m / data_w * w

        # Radar is always centred at data (0, 0)
        cx, cy = data_to_pixel(0.0, 0.0)

        max_range_m = min(abs(x_min), abs(x_max), abs(y_min), abs(y_max))
        ring_interval = RadarRenderer._pick_ring_interval(max_range_m)
        ring_radii_m = [
            ring_interval * i
            for i in range(1, int(max_range_m / ring_interval) + 1)
            if ring_interval * i <= max_range_m * 1.01
        ]

        # --- draw on a transparent overlay so we can composite with alpha ---
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        RING_COLOR = (220, 220, 220, 80)    # soft white, semi-transparent
        CROSS_COLOR = (220, 220, 220, 60)   # slightly more transparent
        LABEL_COLOR = (255, 255, 255, 200)  # near-opaque white text
        RING_WIDTH = 1
        CROSS_WIDTH = 1

        # Attempt to load a small font; fall back to PIL's built-in bitmap font
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size=11)
        except (OSError, IOError):
            try:
                font = ImageFont.truetype("arial.ttf", size=11)
            except (OSError, IOError):
                font = ImageFont.load_default()

        # --- Range rings ---
        for r_m in ring_radii_m:
            r_px = metres_to_px_radius(r_m)
            bbox = [cx - r_px, cy - r_px, cx + r_px, cy + r_px]
            draw.ellipse(bbox, outline=RING_COLOR, width=RING_WIDTH)

            # Label at the top of the ring (12 o'clock position)
            label = f"{int(round(r_m / 1000))} km"
            label_x = cx
            label_y = cy - r_px

            # Centre the label horizontally on the ring top point
            try:
                bbox_text = draw.textbbox((0, 0), label, font=font)
                text_w = bbox_text[2] - bbox_text[0]
                text_h = bbox_text[3] - bbox_text[1]
            except AttributeError:
                # Older PIL versions without textbbox
                text_w, text_h = draw.textsize(label, font=font)  # type: ignore[attr-defined]

            draw.text(
                (label_x - text_w / 2, label_y - text_h - 2),
                label,
                fill=LABEL_COLOR,
                font=font,
            )

        # --- Crosshair ---
        # Horizontal line across full image
        draw.line([(0, cy), (w - 1, cy)], fill=CROSS_COLOR, width=CROSS_WIDTH)
        # Vertical line across full image
        draw.line([(cx, 0), (cx, h - 1)], fill=CROSS_COLOR, width=CROSS_WIDTH)

        # Composite overlay onto the radar image
        result = Image.alpha_composite(img, overlay)
        return result

    def _empty_result(
        self, variable: str, sweep: int, width: int, height: int
    ) -> dict[str, Any]:
        img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format=PNG_FORMAT)
        buf.seek(0)
        return {
            "image": base64.b64encode(buf.getvalue()).decode("ascii"),
            "metadata": {"variable": variable, "sweep": sweep, "width": width, "height": height},
        }

    @staticmethod
    def _extract_polar_coords(
        data: xr.DataArray,
    ) -> tuple[np.ndarray, np.ndarray]:
        if "azimuth" in data.coords:
            azimuth = data.coords["azimuth"].values.astype(float)
        elif "azimuth" in data.dims:
            azimuth = np.arange(data.sizes["azimuth"], dtype=float)
        else:
            azimuth = np.arange(data.shape[0], dtype=float)

        if "range" in data.coords:
            range_m = data.coords["range"].values.astype(float)
        elif "range" in data.dims:
            range_m = np.arange(data.sizes.get("range", data.shape[-1]), dtype=float)
        else:
            range_m = np.arange(data.shape[-1], dtype=float)

        return azimuth, range_m
