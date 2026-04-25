<script lang="ts">
  /**
   * CrossSectionLine -- SVG overlay on the PPI viewer.
   * Click two points to define a cross-section line.
   * Draggable endpoints with A/B labels.
   */
  import { crossSectionStore, type CrossSectionLinePoints } from '../stores/crossSection';
  import { createEventDispatcher } from 'svelte';

  export let active: boolean = false;
  export let scale: number = 1;
  export let translateX: number = 0;
  export let translateY: number = 0;
  export let maxRange: number = 1;
  export let containerWidth: number = 800;
  export let containerHeight: number = 600;

  /** Radar location for coordinate conversion */
  export let radarLat: number = 0;
  export let radarLon: number = 0;

  const dispatch = createEventDispatcher<{
    lineDrawn: CrossSectionLinePoints;
    requestCrossSection: CrossSectionLinePoints;
  }>();

  // Drawing state
  let phase: 'idle' | 'first-placed' | 'complete' = 'idle';
  let startPx = { x: 0, y: 0 };
  let endPx = { x: 0, y: 0 };
  let draggingEndpoint: 'start' | 'end' | null = null;
  let mouseX = 0;
  let mouseY = 0;

  // Sync from store
  $: {
    const line = $crossSectionStore.line;
    if (line) {
      startPx = { x: line.startX, y: line.startY };
      endPx = { x: line.endX, y: line.endY };
      phase = 'complete';
    }
  }

  // Data coordinate conversion helpers
  function pxToData(px: number, py: number): [number, number] {
    // Convert pixel -> NDC -> data (metres from radar)
    const ndcX = (px / containerWidth) * 2 - 1;
    const ndcY = 1 - (py / containerHeight) * 2;
    const aspect = containerWidth / containerHeight;
    const sx = (aspect >= 1 ? scale / aspect : scale) / maxRange;
    const sy = (aspect >= 1 ? scale : scale * aspect) / maxRange;
    const xm = (ndcX - translateX * sx) / sx;
    const ym = (ndcY - translateY * sy) / sy;
    return [xm, ym];
  }

  function dataToLatLon(xm: number, ym: number): [number, number] {
    const lat = radarLat + ym / 110540.0;
    const lon = radarLon + xm / (111320.0 * Math.cos(radarLat * Math.PI / 180));
    return [lat, lon];
  }

  function handleClick(e: MouseEvent) {
    if (!active || draggingEndpoint) return;
    const rect = (e.currentTarget as SVGElement).getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;

    if (phase === 'idle') {
      startPx = { x: px, y: py };
      phase = 'first-placed';
    } else if (phase === 'first-placed') {
      endPx = { x: px, y: py };
      phase = 'complete';
      emitLine();
    }
  }

  function handleMouseMove(e: MouseEvent) {
    const rect = (e.currentTarget as SVGElement).getBoundingClientRect();
    mouseX = e.clientX - rect.left;
    mouseY = e.clientY - rect.top;

    if (draggingEndpoint) {
      if (draggingEndpoint === 'start') {
        startPx = { x: mouseX, y: mouseY };
      } else {
        endPx = { x: mouseX, y: mouseY };
      }
    }
  }

  function handleMouseUp() {
    if (draggingEndpoint) {
      draggingEndpoint = null;
      emitLine();
    }
  }

  function startDragEndpoint(which: 'start' | 'end', e: MouseEvent) {
    e.stopPropagation();
    draggingEndpoint = which;
  }

  function emitLine() {
    const [sxm, sym] = pxToData(startPx.x, startPx.y);
    const [exm, eym] = pxToData(endPx.x, endPx.y);
    const [sLat, sLon] = dataToLatLon(sxm, sym);
    const [eLat, eLon] = dataToLatLon(exm, eym);

    const line: CrossSectionLinePoints = {
      startX: startPx.x,
      startY: startPx.y,
      endX: endPx.x,
      endY: endPx.y,
      startLat: sLat,
      startLon: sLon,
      endLat: eLat,
      endLon: eLon,
    };

    crossSectionStore.update((s) => ({ ...s, line }));
    dispatch('lineDrawn', line);
  }

  function requestCrossSection() {
    const line = $crossSectionStore.line;
    if (line) dispatch('requestCrossSection', line);
  }

  function clearLine() {
    phase = 'idle';
    crossSectionStore.update((s) => ({ ...s, line: null }));
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<svg
  class="cs-line-overlay"
  class:active
  on:click={handleClick}
  on:mousemove={handleMouseMove}
  on:mouseup={handleMouseUp}
>
  {#if phase === 'first-placed'}
    <!-- Rubber-band line from start to cursor -->
    <line
      x1={startPx.x} y1={startPx.y}
      x2={mouseX} y2={mouseY}
      class="cs-line rubber"
    />
    <circle cx={startPx.x} cy={startPx.y} r="6" class="cs-endpoint" />
    <text x={startPx.x - 12} y={startPx.y - 10} class="cs-label">A</text>
  {/if}

  {#if phase === 'complete'}
    <!-- Drawn line -->
    <line
      x1={startPx.x} y1={startPx.y}
      x2={endPx.x} y2={endPx.y}
      class="cs-line"
    />

    <!-- Start endpoint (draggable) -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <circle
      cx={startPx.x} cy={startPx.y} r="7"
      class="cs-endpoint draggable"
      on:mousedown={(e) => startDragEndpoint('start', e)}
    />
    <text x={startPx.x - 14} y={startPx.y - 12} class="cs-label">A</text>

    <!-- End endpoint (draggable) -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <circle
      cx={endPx.x} cy={endPx.y} r="7"
      class="cs-endpoint draggable"
      on:mousedown={(e) => startDragEndpoint('end', e)}
    />
    <text x={endPx.x + 10} y={endPx.y - 12} class="cs-label">B</text>
  {/if}
</svg>

{#if active && phase === 'complete'}
  <div class="cs-line-actions">
    <button class="cs-action-btn primary" on:click={requestCrossSection} title="Request cross-section along this line">
      Request Cross-Section
    </button>
    <button class="cs-action-btn" on:click={clearLine} title="Clear line and redraw">
      Clear
    </button>
  </div>
{/if}

<style>
  .cs-line-overlay {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    z-index: 20;
  }

  .cs-line-overlay.active {
    pointer-events: all;
    cursor: crosshair;
  }

  .cs-line {
    stroke: var(--accent-primary, #5b6cf7);
    stroke-width: 2.5;
    stroke-dasharray: none;
    filter: drop-shadow(0 0 4px rgba(91, 108, 247, 0.5));
  }

  .cs-line.rubber {
    stroke-dasharray: 6 4;
    opacity: 0.7;
  }

  .cs-endpoint {
    fill: var(--accent-primary, #5b6cf7);
    stroke: white;
    stroke-width: 2;
    filter: drop-shadow(0 0 3px rgba(91, 108, 247, 0.6));
  }

  .cs-endpoint.draggable {
    cursor: grab;
    pointer-events: all;
  }

  .cs-endpoint.draggable:active {
    cursor: grabbing;
  }

  .cs-label {
    fill: white;
    font-size: 12px;
    font-weight: 700;
    font-family: var(--font-sans, sans-serif);
    text-shadow: 0 0 6px rgba(0, 0, 0, 0.8);
    pointer-events: none;
  }

  .cs-line-actions {
    position: absolute;
    bottom: 60px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    gap: 8px;
    z-index: 25;
    pointer-events: auto;
  }

  .cs-action-btn {
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
    border-radius: var(--radius-sm, 4px);
    border: 1px solid var(--glass-border, rgba(255,255,255,0.1));
    background: rgba(15, 15, 26, 0.85);
    color: var(--text-secondary, rgba(255,255,255,0.7));
    cursor: pointer;
    backdrop-filter: blur(8px);
    transition: all 150ms ease;
  }

  .cs-action-btn:hover {
    background: rgba(91, 108, 247, 0.15);
    border-color: rgba(91, 108, 247, 0.3);
    color: var(--text-primary, #fff);
  }

  .cs-action-btn.primary {
    background: rgba(91, 108, 247, 0.2);
    border-color: rgba(91, 108, 247, 0.4);
    color: var(--accent-hover, #7b8aff);
  }

  .cs-action-btn.primary:hover {
    background: rgba(91, 108, 247, 0.35);
    color: white;
  }
</style>
