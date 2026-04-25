<script lang="ts">
  /**
   * BoxSelectTool — SVG overlay on the PPI view that lets users draw
   * a rectangular selection box for 3D volume rendering.
   *
   * Dispatches 'box-selected' with {xMin, xMax, yMin, yMax} in meters
   * from radar center.
   */
  import { createEventDispatcher } from 'svelte';

  export let active: boolean = false;
  export let radarRange: number = 150000; // max range in meters
  export let canvasWidth: number = 800;
  export let canvasHeight: number = 800;

  const dispatch = createEventDispatcher<{
    'box-selected': { xMin: number; xMax: number; yMin: number; yMax: number };
  }>();

  let dragging = false;
  let startX = 0;
  let startY = 0;
  let currentX = 0;
  let currentY = 0;

  // Convert pixel coords to meters from radar center.
  // PPI maps [-radarRange, +radarRange] to [0, canvasWidth] on x
  // and [canvasHeight, 0] on y (north up).
  function pixToMetersX(px: number): number {
    return ((px / canvasWidth) * 2 - 1) * radarRange;
  }

  function pixToMetersY(py: number): number {
    return (1 - (py / canvasHeight) * 2) * radarRange;
  }

  // Derived rectangle in pixel space
  $: rectX = Math.min(startX, currentX);
  $: rectY = Math.min(startY, currentY);
  $: rectW = Math.abs(currentX - startX);
  $: rectH = Math.abs(currentY - startY);

  function onMouseDown(e: MouseEvent) {
    if (!active) return;
    dragging = true;
    const svg = e.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    startX = e.clientX - rect.left;
    startY = e.clientY - rect.top;
    currentX = startX;
    currentY = startY;
  }

  function onMouseMove(e: MouseEvent) {
    if (!dragging) return;
    const svg = (e.currentTarget ?? e.target) as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    currentX = Math.max(0, Math.min(canvasWidth, e.clientX - rect.left));
    currentY = Math.max(0, Math.min(canvasHeight, e.clientY - rect.top));
  }

  function onMouseUp() {
    if (!dragging) return;
    dragging = false;

    // Require a minimum selection size (at least 5px)
    if (rectW < 5 || rectH < 5) return;

    const xMin = pixToMetersX(Math.min(startX, currentX));
    const xMax = pixToMetersX(Math.max(startX, currentX));
    const yMin = pixToMetersY(Math.max(startY, currentY)); // max pixel Y = min meters Y
    const yMax = pixToMetersY(Math.min(startY, currentY));

    dispatch('box-selected', { xMin, xMax, yMin, yMax });
  }
</script>

{#if active}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <svg
    class="box-select-overlay"
    width={canvasWidth}
    height={canvasHeight}
    on:mousedown={onMouseDown}
    on:mousemove={onMouseMove}
    on:mouseup={onMouseUp}
    on:mouseleave={onMouseUp}
  >
    {#if dragging && rectW > 0 && rectH > 0}
      <rect
        x={rectX}
        y={rectY}
        width={rectW}
        height={rectH}
        class="selection-rect"
      />
      <!-- Corner handles -->
      <circle cx={rectX} cy={rectY} r="3" class="corner-handle" />
      <circle cx={rectX + rectW} cy={rectY} r="3" class="corner-handle" />
      <circle cx={rectX} cy={rectY + rectH} r="3" class="corner-handle" />
      <circle cx={rectX + rectW} cy={rectY + rectH} r="3" class="corner-handle" />
    {/if}
  </svg>
{/if}

<style>
  .box-select-overlay {
    position: absolute;
    top: 0;
    left: 0;
    cursor: crosshair;
    z-index: 20;
    pointer-events: all;
  }

  .selection-rect {
    fill: rgba(0, 220, 255, 0.1);
    stroke: rgba(0, 220, 255, 0.8);
    stroke-width: 1.5;
    stroke-dasharray: 6 3;
  }

  .corner-handle {
    fill: rgba(0, 220, 255, 0.9);
    stroke: none;
  }
</style>
