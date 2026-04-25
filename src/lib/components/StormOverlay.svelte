<script lang="ts">
  /**
   * StormOverlay — renders storm cell boundaries, IDs, and max dBZ labels
   * as HTML overlays positioned over the PPI canvas.
   *
   * Uses absolute positioning relative to the viewer-canvas container.
   * Cells are projected from polar (azimuth, range) to screen coordinates
   * using the same transform the WebGL PPI uses.
   */
  import { stormCells, type CellData } from '../stores/stormCells';

  // View parameters — must match RadarViewer's current state
  export let scale: number = 1;
  export let translateX: number = 0;
  export let translateY: number = 0;
  export let maxRange: number = 1;
  export let containerWidth: number = 800;
  export let containerHeight: number = 600;

  let cells: CellData[] = [];
  let visible: boolean = true;
  let selectedId: string | null = null;

  $: cells = $stormCells.cells;
  $: visible = $stormCells.overlayVisible;
  $: selectedId = $stormCells.selectedCellId;

  /**
   * Convert polar (azimuth_deg, range_m) to screen pixel coordinates.
   * Mirrors the WebGL vertex transform in ppiRenderer.
   */
  function polarToScreen(azDeg: number, rangeM: number): { x: number; y: number } {
    const azRad = (azDeg * Math.PI) / 180;
    // Data-space: x = range * sin(az), y = range * cos(az)
    const dataX = rangeM * Math.sin(azRad);
    const dataY = rangeM * Math.cos(azRad);

    const aspect = containerWidth / containerHeight;
    const sx = (aspect >= 1 ? scale / aspect : scale) / maxRange;
    const sy = (aspect >= 1 ? scale : scale * aspect) / maxRange;

    const ndcX = (dataX + translateX) * sx;
    const ndcY = (dataY + translateY) * sy;

    // NDC [-1,1] to pixel
    const px = ((ndcX + 1) / 2) * containerWidth;
    const py = ((1 - ndcY) / 2) * containerHeight;
    return { x: px, y: py };
  }

  function selectCell(id: string) {
    stormCells.update((s) => ({
      ...s,
      selectedCellId: s.selectedCellId === id ? null : id,
    }));
  }

  function dbzColor(dbz: number): string {
    if (dbz >= 60) return '#ff0040';
    if (dbz >= 50) return '#ff4000';
    if (dbz >= 45) return '#ff8000';
    if (dbz >= 40) return '#ffcc00';
    return '#00ccff';
  }

  /**
   * Build an SVG polygon path from boundary points for a cell.
   */
  function boundaryPath(cell: CellData): string {
    if (cell.boundary_points.length < 3) return '';
    const pts = cell.boundary_points.map(([az, rng]) => polarToScreen(az, rng));
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ') + ' Z';
  }
</script>

{#if visible && cells.length > 0}
  <div class="storm-overlay" aria-hidden="true">
    <!-- SVG layer for boundaries -->
    <svg class="boundary-svg" viewBox="0 0 {containerWidth} {containerHeight}">
      {#each cells as cell (cell.id)}
        {@const path = boundaryPath(cell)}
        {#if path}
          <path
            d={path}
            class="cell-boundary"
            class:selected={cell.id === selectedId}
            style="stroke: {dbzColor(cell.max_dbz)}"
          />
        {/if}
      {/each}
    </svg>

    <!-- HTML labels for each cell -->
    {#each cells as cell (cell.id)}
      {@const pos = polarToScreen(cell.centroid_az, cell.centroid_range)}
      <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
      <div
        class="cell-label"
        class:selected={cell.id === selectedId}
        style="left: {pos.x}px; top: {pos.y}px; --cell-color: {dbzColor(cell.max_dbz)}"
        on:click|stopPropagation={() => selectCell(cell.id)}
        title="{cell.id}: {cell.max_dbz.toFixed(1)} dBZ, {cell.area_km2.toFixed(1)} km2"
      >
        <span class="cell-id">{cell.id}</span>
        <span class="cell-dbz">{cell.max_dbz.toFixed(0)}</span>
      </div>
    {/each}
  </div>
{/if}

<style>
  .storm-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 20;
    overflow: hidden;
  }

  .boundary-svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }

  .cell-boundary {
    fill: none;
    stroke-width: 1.5;
    stroke-opacity: 0.7;
    stroke-dasharray: 4 2;
    transition: stroke-opacity 150ms ease, stroke-width 150ms ease;
  }

  .cell-boundary.selected {
    stroke-width: 2.5;
    stroke-opacity: 1;
    stroke-dasharray: none;
  }

  .cell-label {
    position: absolute;
    transform: translate(-50%, -50%);
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 2px 6px;
    border-radius: 3px;
    background: rgba(15, 15, 26, 0.82);
    border: 1px solid var(--cell-color, #00ccff);
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-primary, #e8e8ee);
    white-space: nowrap;
    pointer-events: auto;
    cursor: pointer;
    backdrop-filter: blur(3px);
    transition: transform 150ms ease, border-color 150ms ease;
    user-select: none;
  }

  .cell-label:hover {
    transform: translate(-50%, -50%) scale(1.1);
    z-index: 5;
  }

  .cell-label.selected {
    border-width: 2px;
    background: rgba(15, 15, 26, 0.92);
    z-index: 10;
  }

  .cell-id {
    font-weight: 700;
    color: var(--cell-color, #00ccff);
  }

  .cell-dbz {
    color: var(--text-secondary, #a0a0b8);
    font-size: 9px;
  }
</style>
