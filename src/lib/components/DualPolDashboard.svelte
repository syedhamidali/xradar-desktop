<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import ScatterPlot from './ScatterPlot.svelte';
  import HistogramChart from './HistogramChart.svelte';
  import { wsManager } from '../utils/websocket';
  import { radarData, selectedVariable, selectedSweep, connectionStatus } from '../stores/radarData';

  // ── State ──────────────────────────────────────────────────────────────

  // Variable pair selection
  const COMMON_PAIRS: [string, string][] = [
    ['DBZH', 'ZDR'],
    ['DBZH', 'KDP'],
    ['ZDR', 'KDP'],
    ['DBZH', 'RHOHV'],
  ];
  let selectedPairIdx = $state(0);
  let scatterVar1 = $state('DBZH');
  let scatterVar2 = $state('ZDR');
  let histVar = $state('DBZH');
  let colorVar: string | null = $state(null);

  // HID classes and colors
  const HID_CLASSES = [
    { name: 'Drizzle', color: '#22d3ee', id: 0 },
    { name: 'Rain', color: '#3b82f6', id: 1 },
    { name: 'Wet Snow', color: '#a78bfa', id: 2 },
    { name: 'Dry Snow', color: '#e2e8f0', id: 3 },
    { name: 'Graupel', color: '#f59e0b', id: 4 },
    { name: 'Hail', color: '#ef4444', id: 5 },
  ];

  // Region stats
  let regionStats: Record<string, any> | null = $state(null);
  let regionLoading = $state(false);

  // Histogram controls
  let binCount = $state(50);
  let showGaussian = $state(true);
  let densityMode = $state(false);

  // PPI thumbnail state
  let ppiThumbnail: string | null = $state(null);

  // Layout
  let containerEl: HTMLDivElement | undefined = $state(undefined);
  let containerWidth = $state(800);
  let containerHeight = $state(600);
  let resizeObs: ResizeObserver | null = null;

  let unsubRegionStats: (() => void) | null = null;

  // ── Derived ────────────────────────────────────────────────────────────
  const variables = $derived($radarData.variables);
  const isConnected = $derived($connectionStatus === 'connected');
  const cellW = $derived(Math.floor((containerWidth - 24) / 2));
  const cellH = $derived(Math.floor((containerHeight - 110) / 2));

  // ── Effects ────────────────────────────────────────────────────────────
  $effect(() => {
    if (selectedPairIdx >= 0 && selectedPairIdx < COMMON_PAIRS.length) {
      scatterVar1 = COMMON_PAIRS[selectedPairIdx][0];
      scatterVar2 = COMMON_PAIRS[selectedPairIdx][1];
    }
  });

  // ── Lifecycle ──────────────────────────────────────────────────────────
  onMount(() => {
    resizeObs = new ResizeObserver((entries) => {
      for (const entry of entries) {
        containerWidth = Math.round(entry.contentRect.width);
        containerHeight = Math.round(entry.contentRect.height);
      }
    });
    if (containerEl) resizeObs.observe(containerEl);

    unsubRegionStats = wsManager.onMessage('region_stats_result', (msg: any) => {
      regionStats = msg;
      regionLoading = false;
    });
  });

  onDestroy(() => {
    resizeObs?.disconnect();
    unsubRegionStats?.();
  });

  // ── Handlers ───────────────────────────────────────────────────────────
  function onPairChange(e: Event) {
    selectedPairIdx = parseInt((e.target as HTMLSelectElement).value, 10);
  }

  function onHistVarChange(e: Event) {
    histVar = (e.target as HTMLSelectElement).value;
  }

  function onColorVarChange(e: Event) {
    const v = (e.target as HTMLSelectElement).value;
    colorVar = v === '' ? null : v;
  }

  function onBinChange(e: Event) {
    binCount = parseInt((e.target as HTMLInputElement).value, 10);
  }

  function requestRegionStats(azMin: number, azMax: number, rangeMin: number, rangeMax: number) {
    regionLoading = true;
    wsManager.send({
      type: 'get_region_stats',
      variable: histVar,
      sweep: $selectedSweep,
      az_min: azMin,
      az_max: azMax,
      range_min: rangeMin,
      range_max: rangeMax,
    });
  }

  function onScatterSelection(e: CustomEvent) {
    // Lasso selection from scatter plot - could be used to highlight on PPI
    const indices = e.detail.indices;
    if (indices && indices.length > 0) {
      // Could dispatch to PPI in a future integration
      console.log(`[DualPol] Lasso selected ${indices.length} points`);
    }
  }

  function onHistRangeSelect(e: CustomEvent) {
    const { min, max, variable: v } = e.detail;
    if (min !== null && max !== null) {
      console.log(`[DualPol] Histogram range: ${v} [${min.toFixed(2)}, ${max.toFixed(2)}]`);
    }
  }

  function fmtStat(v: number | undefined): string {
    if (v === undefined || v === null) return '---';
    return typeof v === 'number' ? v.toFixed(2) : String(v);
  }
</script>

<div class="dualpol-dashboard" bind:this={containerEl}>
  <!-- Controls bar -->
  <div class="dp-controls">
    <div class="dp-ctrl-group">
      <label for="dp-pair">Pair</label>
      <select id="dp-pair" value={selectedPairIdx} on:change={onPairChange} disabled={!isConnected}>
        {#each COMMON_PAIRS as pair, i}
          <option value={i}>{pair[0]} / {pair[1]}</option>
        {/each}
      </select>
    </div>

    <div class="dp-ctrl-group">
      <label for="dp-histvar">Histogram</label>
      <select id="dp-histvar" value={histVar} on:change={onHistVarChange} disabled={!isConnected}>
        {#each variables as v}
          <option value={v}>{v}</option>
        {/each}
      </select>
    </div>

    <div class="dp-ctrl-group">
      <label for="dp-colorvar">Color by</label>
      <select id="dp-colorvar" value={colorVar ?? ''} on:change={onColorVarChange} disabled={!isConnected}>
        <option value="">None</option>
        {#each variables as v}
          <option value={v}>{v}</option>
        {/each}
      </select>
    </div>

    <div class="dp-ctrl-group">
      <label for="dp-bins">Bins ({binCount})</label>
      <input id="dp-bins" type="range" min="10" max="200" step="5" value={binCount} on:input={onBinChange} />
    </div>

    <div class="dp-ctrl-group">
      <label class="dp-toggle">
        <input type="checkbox" bind:checked={showGaussian} />
        Gaussian
      </label>
      <label class="dp-toggle">
        <input type="checkbox" bind:checked={densityMode} />
        Density
      </label>
    </div>
  </div>

  <!-- 2x2 grid -->
  <div class="dp-grid">
    <!-- Top left: HID Legend -->
    <div class="dp-cell">
      <div class="dp-cell-header">Hydrometeor ID Legend</div>
      <div class="hid-legend">
        {#each HID_CLASSES as cls}
          <div class="hid-item">
            <span class="hid-swatch" style="background:{cls.color}"></span>
            <span class="hid-name">{cls.name}</span>
          </div>
        {/each}
      </div>

      <!-- Region stats (if available) -->
      {#if regionStats}
        <div class="region-stats-box">
          <div class="dp-cell-header">Region Stats ({regionStats.variable})</div>
          {#if regionStats.stats}
            <div class="rs-grid">
              <span class="rs-label">Mean</span><span class="rs-value">{fmtStat(regionStats.stats.mean)}</span>
              <span class="rs-label">Std</span><span class="rs-value">{fmtStat(regionStats.stats.std)}</span>
              <span class="rs-label">Min</span><span class="rs-value">{fmtStat(regionStats.stats.min)}</span>
              <span class="rs-label">Max</span><span class="rs-value">{fmtStat(regionStats.stats.max)}</span>
              <span class="rs-label">Count</span><span class="rs-value">{regionStats.stats.count ?? regionStats.n_valid}</span>
            </div>
          {:else}
            <div class="rs-empty">No data in selected region</div>
          {/if}
        </div>
      {/if}

      <!-- Quick region selector -->
      <div class="region-selector">
        <div class="dp-cell-header">Quick Region Query</div>
        <div class="region-form">
          <button class="dp-btn" on:click={() => requestRegionStats(0, 90, 0, 150000)} disabled={!isConnected}>NE Quad</button>
          <button class="dp-btn" on:click={() => requestRegionStats(90, 180, 0, 150000)} disabled={!isConnected}>SE Quad</button>
          <button class="dp-btn" on:click={() => requestRegionStats(180, 270, 0, 150000)} disabled={!isConnected}>SW Quad</button>
          <button class="dp-btn" on:click={() => requestRegionStats(270, 360, 0, 150000)} disabled={!isConnected}>NW Quad</button>
          <button class="dp-btn" on:click={() => requestRegionStats(0, 360, 0, 50000)} disabled={!isConnected}>Near (0-50km)</button>
          <button class="dp-btn" on:click={() => requestRegionStats(0, 360, 50000, 150000)} disabled={!isConnected}>Far (50-150km)</button>
        </div>
      </div>
    </div>

    <!-- Top right: Scatter plot -->
    <div class="dp-cell">
      <div class="dp-cell-header">Scatter: {scatterVar1} vs {scatterVar2}</div>
      <ScatterPlot
        var1={scatterVar1}
        var2={scatterVar2}
        {colorVar}
        {densityMode}
        width={cellW}
        height={cellH - 20}
        on:selection={onScatterSelection}
      />
    </div>

    <!-- Bottom left: Histogram -->
    <div class="dp-cell">
      <div class="dp-cell-header">Distribution: {histVar}</div>
      <HistogramChart
        variable={histVar}
        bins={binCount}
        {showGaussian}
        width={cellW}
        height={cellH - 20}
        on:rangeSelect={onHistRangeSelect}
      />
    </div>

    <!-- Bottom right: Scatter (second pair or rotated view) -->
    <div class="dp-cell">
      <div class="dp-cell-header">Scatter: {scatterVar2} vs {scatterVar1}</div>
      <ScatterPlot
        var1={scatterVar2}
        var2={scatterVar1}
        {colorVar}
        {densityMode}
        width={cellW}
        height={cellH - 20}
        on:selection={onScatterSelection}
      />
    </div>
  </div>
</div>

<style>
  .dualpol-dashboard {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-primary, #0f0f1a);
    overflow: hidden;
  }

  .dp-controls {
    display: flex;
    align-items: center;
    gap: var(--spacing-lg, 16px);
    padding: var(--spacing-sm, 6px) var(--spacing-md, 12px);
    background: var(--bg-secondary, #1a1a2e);
    border-bottom: 1px solid var(--border-color, #2a2a3e);
    flex-shrink: 0;
    flex-wrap: wrap;
  }

  .dp-ctrl-group {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm, 6px);
  }

  .dp-ctrl-group label {
    font-size: 10px;
    color: var(--text-muted, #6a6a80);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    font-weight: 600;
    white-space: nowrap;
  }

  .dp-ctrl-group select {
    min-width: 90px;
    font-size: 11px;
  }

  .dp-ctrl-group input[type="range"] {
    width: 80px;
    accent-color: var(--accent-primary, #5b6cf7);
  }

  .dp-toggle {
    display: flex;
    align-items: center;
    gap: 4px;
    cursor: pointer;
    font-size: 10px !important;
  }

  .dp-toggle input[type="checkbox"] {
    accent-color: var(--accent-primary, #5b6cf7);
  }

  .dp-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: 1fr 1fr;
    gap: 8px;
    padding: 8px;
    flex: 1;
    overflow: hidden;
  }

  .dp-cell {
    display: flex;
    flex-direction: column;
    background: var(--bg-secondary, #1a1a2e);
    border: 1px solid var(--border-color, #2a2a3e);
    border-radius: var(--radius-md, 6px);
    overflow: hidden;
  }

  .dp-cell-header {
    padding: 4px 8px;
    font-size: 10px;
    font-weight: 600;
    color: var(--text-secondary, #a0a0b0);
    background: var(--bg-primary, #0f0f1a);
    border-bottom: 1px solid var(--border-color, #2a2a3e);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    flex-shrink: 0;
  }

  /* HID Legend */
  .hid-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 8px;
  }

  .hid-item {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .hid-swatch {
    width: 12px;
    height: 12px;
    border-radius: 2px;
    border: 1px solid rgba(255, 255, 255, 0.1);
  }

  .hid-name {
    font-size: 10px;
    color: var(--text-primary, #e0e0f0);
    font-family: var(--font-mono, monospace);
  }

  /* Region stats */
  .region-stats-box {
    border-top: 1px solid var(--border-color, #2a2a3e);
  }

  .rs-grid {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 2px 8px;
    padding: 4px 8px;
    font-size: 10px;
    font-family: var(--font-mono, monospace);
  }

  .rs-label {
    color: var(--text-muted, #6a6a80);
  }

  .rs-value {
    color: var(--text-primary, #e0e0f0);
  }

  .rs-empty {
    padding: 4px 8px;
    font-size: 10px;
    color: var(--text-muted, #6a6a80);
    font-style: italic;
  }

  /* Region selector */
  .region-selector {
    border-top: 1px solid var(--border-color, #2a2a3e);
    flex: 1;
  }

  .region-form {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 6px 8px;
  }

  .dp-btn {
    padding: 3px 8px;
    font-size: 9px;
    font-family: var(--font-mono, monospace);
    background: var(--bg-primary, #0f0f1a);
    color: var(--text-secondary, #a0a0b0);
    border: 1px solid var(--border-color, #2a2a3e);
    border-radius: var(--radius-sm, 3px);
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }

  .dp-btn:hover:not(:disabled) {
    background: var(--accent-primary, #5b6cf7);
    color: var(--text-primary, #e0e0f0);
    border-color: var(--accent-primary, #5b6cf7);
  }

  .dp-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
