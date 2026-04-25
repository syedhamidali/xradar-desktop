<script lang="ts">
  /**
   * MapBackground — renders slippy-map tiles on a canvas behind the radar PPI.
   *
   * Uses CartoDB basemap tiles (dark/light) with no API key required.
   * Tiles are fetched, cached, and painted in sync with the PPI view transform.
   */
  import { onMount, onDestroy } from 'svelte';
  import { settings } from '../stores/settings';
  import {
    latLonToTile,
    tileToLatLon,
    computeTileZoom,
    latLonToPixel,
  } from '../utils/geoProjection';

  export let radarLat: number;
  export let radarLon: number;
  export let maxRange: number;
  export let scale: number;
  export let translateX: number;
  export let translateY: number;
  export let containerWidth: number;
  export let containerHeight: number;
  export let opacity: number = 0.6;

  let canvasEl: HTMLCanvasElement;
  let ctx: CanvasRenderingContext2D | null = null;
  let themeMode: 'dark' | 'light' | 'auto' = 'dark';
  let rafId: number = 0;

  // Tile image cache: "z/x/y" -> HTMLImageElement
  const tileCache = new Map<string, HTMLImageElement>();
  const loadingTiles = new Set<string>();
  const TILE_SIZE = 256;
  const MAX_CACHE = 512;

  $: themeMode = $settings.theme.mode;

  // Determine tile URL template based on theme
  $: tileUrlBase =
    themeMode === 'light'
      ? 'https://a.basemaps.cartocdn.com/light_all'
      : 'https://a.basemaps.cartocdn.com/dark_all';

  // Re-render whenever view params change
  $: if (ctx && radarLat != null && radarLon != null) {
    // Trigger reactive update on all view params
    void (scale, translateX, translateY, containerWidth, containerHeight, maxRange, opacity, tileUrlBase);
    scheduleRender();
  }

  onMount(() => {
    ctx = canvasEl.getContext('2d');
    scheduleRender();
  });

  onDestroy(() => {
    if (rafId) cancelAnimationFrame(rafId);
  });

  function scheduleRender() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(renderTiles);
  }

  function tileUrl(z: number, x: number, y: number): string {
    return `${tileUrlBase}/${z}/${x}/${y}.png`;
  }

  function tileKey(z: number, x: number, y: number): string {
    return `${z}/${x}/${y}`;
  }

  function loadTile(z: number, x: number, y: number): HTMLImageElement | null {
    const key = tileKey(z, x, y);
    if (tileCache.has(key)) return tileCache.get(key)!;
    if (loadingTiles.has(key)) return null;

    loadingTiles.add(key);
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      loadingTiles.delete(key);
      // Evict oldest if cache too large
      if (tileCache.size >= MAX_CACHE) {
        const first = tileCache.keys().next().value;
        if (first !== undefined) tileCache.delete(first);
      }
      tileCache.set(key, img);
      scheduleRender();
    };
    img.onerror = () => {
      loadingTiles.delete(key);
    };
    img.src = tileUrl(z, x, y);
    return null;
  }

  function renderTiles() {
    rafId = 0;
    if (!ctx || !canvasEl || radarLat == null || radarLon == null) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cw = containerWidth;
    const ch = containerHeight;
    canvasEl.width = Math.round(cw * dpr);
    canvasEl.height = Math.round(ch * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    ctx.globalAlpha = opacity;

    const zoom = computeTileZoom(maxRange, scale, cw);

    // Find the lat/lon bounds of the visible area by sampling screen corners
    const corners: [number, number][] = [];
    for (const px of [0, cw]) {
      for (const py of [0, ch]) {
        // Reverse-project screen pixel to data-space, then to lat/lon
        const ndcX = (px / cw) * 2 - 1;
        const ndcY = 1 - (py / ch) * 2;
        const aspect = cw / ch;
        const sx = (aspect >= 1 ? scale / aspect : scale) / maxRange;
        const sy = (aspect >= 1 ? scale : scale * aspect) / maxRange;
        const dataX = ndcX / sx - translateX;
        const dataY = ndcY / sy - translateY;
        // data-space to lat/lon (azimuthal equidistant)
        const cosRadarLat = Math.cos(radarLat * (Math.PI / 180));
        const lat = radarLat + (dataY / 6371000) * (180 / Math.PI);
        const lon = radarLon + (dataX / (6371000 * cosRadarLat)) * (180 / Math.PI);
        corners.push([lat, lon]);
      }
    }

    // Get tile bounds
    const lats = corners.map((c) => c[0]);
    const lons = corners.map((c) => c[1]);
    const minLat = Math.min(...lats);
    const maxLat = Math.max(...lats);
    const minLon = Math.min(...lons);
    const maxLon = Math.max(...lons);

    const topLeft = latLonToTile(maxLat, minLon, zoom);
    const bottomRight = latLonToTile(minLat, maxLon, zoom);

    const tileXMin = Math.max(0, Math.floor(topLeft.tx) - 1);
    const tileXMax = Math.min(Math.pow(2, zoom) - 1, Math.ceil(bottomRight.tx) + 1);
    const tileYMin = Math.max(0, Math.floor(topLeft.ty) - 1);
    const tileYMax = Math.min(Math.pow(2, zoom) - 1, Math.ceil(bottomRight.ty) + 1);

    // Draw each visible tile
    for (let tx = tileXMin; tx <= tileXMax; tx++) {
      for (let ty = tileYMin; ty <= tileYMax; ty++) {
        const img = loadTile(zoom, tx, ty);
        if (!img) continue;

        // Get the four corners of this tile in lat/lon
        const [tlLat, tlLon] = tileToLatLon(tx, ty, zoom);
        const [brLat, brLon] = tileToLatLon(tx + 1, ty + 1, zoom);

        // Project tile corners to screen pixels
        const [tlPx, tlPy] = latLonToPixel(
          tlLat, tlLon, radarLat, radarLon,
          maxRange, scale, translateX, translateY, cw, ch,
        );
        const [brPx, brPy] = latLonToPixel(
          brLat, brLon, radarLat, radarLon,
          maxRange, scale, translateX, translateY, cw, ch,
        );

        const drawW = brPx - tlPx;
        const drawH = brPy - tlPy;

        if (drawW > 0.5 && drawH > 0.5) {
          ctx.drawImage(img, tlPx, tlPy, drawW, drawH);
        }
      }
    }

    ctx.globalAlpha = 1;
  }
</script>

<canvas
  bind:this={canvasEl}
  class="map-background-canvas"
  aria-hidden="true"
></canvas>

<style>
  .map-background-canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    z-index: 0;
    pointer-events: none;
  }
</style>
