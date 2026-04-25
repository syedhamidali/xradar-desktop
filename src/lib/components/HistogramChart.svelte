<script lang="ts">
  import { onMount, onDestroy, createEventDispatcher } from 'svelte';
  import { wsManager } from '../utils/websocket';
  import { selectedSweep } from '../stores/radarData';

  const dispatch = createEventDispatcher();

  // ── Props ──────────────────────────────────────────────────────────────
  export let variable: string = 'DBZH';
  export let bins: number = 50;
  export let showGaussian: boolean = true;
  export let width: number = 400;
  export let height: number = 340;

  // ── Internal state ─────────────────────────────────────────────────────
  let binEdges: number[] = [];
  let counts: number[] = [];
  let gaussianFit: number[] = [];
  let stats: Record<string, number> = {};
  let units = '';
  let nValid = 0;
  let isLoading = false;

  // Range selection (drag to select value range)
  let rangeSelecting = false;
  let rangeStart: number | null = null;
  let rangeEnd: number | null = null;
  let rangeStartPx = 0;

  // Hover
  let hoveredBin: number | null = null;
  let hoverX = 0;
  let hoverY = 0;

  const MARGIN = { top: 10, right: 12, bottom: 38, left: 52 };
  const plotW = () => width - MARGIN.left - MARGIN.right;
  const plotH = () => height - MARGIN.top - MARGIN.bottom;

  let svgEl: SVGSVGElement;
  let unsub: (() => void) | null = null;

  $: sweep = $selectedSweep;

  // Reactive request
  let prevKey = '';
  $: {
    const key = `${variable}:${sweep}:${bins}`;
    if (variable && key !== prevKey) {
      prevKey = key;
      requestData();
    }
  }

  function requestData() {
    isLoading = true;
    wsManager.send({
      type: 'get_histogram',
      variable,
      sweep,
      bins,
    });
  }

  onMount(() => {
    unsub = wsManager.onMessage('histogram_result', (msg: any) => {
      if (msg.variable !== variable) return;
      binEdges = msg.bin_edges || [];
      counts = msg.counts || [];
      gaussianFit = msg.gaussian_fit || [];
      stats = msg.stats || {};
      units = msg.units || '';
      nValid = msg.n_valid || 0;
      isLoading = false;
    });
  });

  onDestroy(() => { unsub?.(); });

  // ── Derived values ─────────────────────────────────────────────────────
  $: maxCount = counts.length > 0 ? Math.max(...counts, 1) : 1;
  $: binCenters = binEdges.length > 1
    ? binEdges.slice(0, -1).map((e, i) => (e + binEdges[i + 1]) / 2)
    : [];
  $: xMin = binEdges.length > 0 ? binEdges[0] : 0;
  $: xMax = binEdges.length > 0 ? binEdges[binEdges.length - 1] : 1;
  $: barWidth = binEdges.length > 1
    ? (plotW() / (binEdges.length - 1))
    : plotW();

  // ── Coordinate transforms ──────────────────────────────────────────────
  function dataToSvgX(v: number): number {
    return MARGIN.left + ((v - xMin) / (xMax - xMin)) * plotW();
  }
  function svgToDataX(px: number): number {
    return xMin + ((px - MARGIN.left) / plotW()) * (xMax - xMin);
  }
  function countToSvgY(c: number): number {
    return MARGIN.top + plotH() - (c / maxCount) * plotH();
  }

  // ── Ticks ──────────────────────────────────────────────────────────────
  function niceStep(range: number, maxTicks: number): number {
    const rough = range / maxTicks;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const norm = rough / mag;
    let nice: number;
    if (norm <= 1.5) nice = 1;
    else if (norm <= 3) nice = 2;
    else if (norm <= 7) nice = 5;
    else nice = 10;
    return nice * mag;
  }

  function makeTicks(lo: number, hi: number, maxTicks: number = 6): number[] {
    if (hi <= lo) return [];
    const step = niceStep(hi - lo, maxTicks);
    const start = Math.ceil(lo / step) * step;
    const ticks: number[] = [];
    for (let t = start; t <= hi; t += step) {
      ticks.push(parseFloat(t.toPrecision(10)));
    }
    return ticks;
  }

  $: xTicks = makeTicks(xMin, xMax);
  $: yTicks = makeTicks(0, maxCount, 5);

  function fmtTick(v: number): string {
    if (Math.abs(v) >= 10000) return v.toExponential(1);
    if (Math.abs(v) < 0.01 && v !== 0) return v.toExponential(1);
    return parseFloat(v.toPrecision(4)).toString();
  }

  // ── Gaussian line path ─────────────────────────────────────────────────
  $: gaussianPath = showGaussian && gaussianFit.length > 0 && binCenters.length > 0
    ? 'M' + binCenters.map((cx, i) =>
        `${dataToSvgX(cx)},${countToSvgY(gaussianFit[i])}`
      ).join('L')
    : '';

  // ── Mouse handlers ─────────────────────────────────────────────────────
  function getSvgCoords(e: MouseEvent): { sx: number; sy: number } {
    const rect = svgEl.getBoundingClientRect();
    return { sx: e.clientX - rect.left, sy: e.clientY - rect.top };
  }

  function onMouseDown(e: MouseEvent) {
    if (e.button !== 0) return;
    const { sx } = getSvgCoords(e);
    rangeSelecting = true;
    rangeStartPx = sx;
    rangeStart = svgToDataX(sx);
    rangeEnd = rangeStart;
  }

  function onMouseMove(e: MouseEvent) {
    const { sx, sy } = getSvgCoords(e);

    if (rangeSelecting) {
      rangeEnd = svgToDataX(sx);
      return;
    }

    // Hover detection
    if (binEdges.length < 2) return;
    const dv = svgToDataX(sx);
    const binIdx = Math.floor(((dv - xMin) / (xMax - xMin)) * counts.length);
    if (binIdx >= 0 && binIdx < counts.length) {
      hoveredBin = binIdx;
      hoverX = sx;
      hoverY = sy;
    } else {
      hoveredBin = null;
    }
  }

  function onMouseUp() {
    if (rangeSelecting && rangeStart !== null && rangeEnd !== null) {
      rangeSelecting = false;
      const lo = Math.min(rangeStart, rangeEnd);
      const hi = Math.max(rangeStart, rangeEnd);
      if (hi - lo > (xMax - xMin) * 0.01) {
        dispatch('rangeSelect', { min: lo, max: hi, variable });
      } else {
        rangeStart = null;
        rangeEnd = null;
      }
    }
  }

  function onDblClick() {
    rangeStart = null;
    rangeEnd = null;
    dispatch('rangeSelect', { min: null, max: null, variable });
  }

  // ── Selection highlight rect ───────────────────────────────────────────
  $: selRectX = rangeStart !== null && rangeEnd !== null
    ? dataToSvgX(Math.min(rangeStart, rangeEnd))
    : 0;
  $: selRectW = rangeStart !== null && rangeEnd !== null
    ? Math.abs(dataToSvgX(rangeEnd) - dataToSvgX(rangeStart))
    : 0;

  // ── Stats format ───────────────────────────────────────────────────────
  function fmtStat(v: number | undefined): string {
    if (v === undefined) return '---';
    return v.toFixed(2);
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div class="histogram-container" style="width:{width}px;height:{height + 60}px">
  {#if isLoading}
    <div class="hist-loading">Loading...</div>
  {/if}

  <svg
    bind:this={svgEl}
    {width}
    {height}
    on:mousedown={onMouseDown}
    on:mousemove={onMouseMove}
    on:mouseup={onMouseUp}
    on:mouseleave={() => { rangeSelecting = false; hoveredBin = null; }}
    on:dblclick={onDblClick}
    class="hist-svg"
  >
    <defs>
      <clipPath id="hist-clip">
        <rect x={MARGIN.left} y={MARGIN.top} width={plotW()} height={plotH()} />
      </clipPath>
    </defs>

    <!-- Background -->
    <rect x={MARGIN.left} y={MARGIN.top} width={plotW()} height={plotH()} fill="var(--bg-primary, #0f0f1a)" />

    <!-- Grid lines -->
    {#each xTicks as t}
      <line x1={dataToSvgX(t)} y1={MARGIN.top} x2={dataToSvgX(t)} y2={MARGIN.top + plotH()}
            stroke="var(--border-color, #2a2a3e)" stroke-width="0.5" />
      <text x={dataToSvgX(t)} y={height - MARGIN.bottom + 16}
            fill="var(--text-muted, #6a6a80)" font-size="9" text-anchor="middle"
            font-family="var(--font-mono, monospace)">{fmtTick(t)}</text>
    {/each}
    {#each yTicks as t}
      <line x1={MARGIN.left} y1={countToSvgY(t)} x2={MARGIN.left + plotW()} y2={countToSvgY(t)}
            stroke="var(--border-color, #2a2a3e)" stroke-width="0.5" />
      <text x={MARGIN.left - 6} y={countToSvgY(t) + 3}
            fill="var(--text-muted, #6a6a80)" font-size="9" text-anchor="end"
            font-family="var(--font-mono, monospace)">{fmtTick(t)}</text>
    {/each}

    <!-- Axis labels -->
    <text x={MARGIN.left + plotW() / 2} y={height - 3}
          fill="var(--text-secondary, #a0a0b0)" font-size="10" text-anchor="middle"
          font-family="var(--font-mono, monospace)">{variable} {units ? `(${units})` : ''}</text>
    <text x={12} y={MARGIN.top + plotH() / 2}
          fill="var(--text-secondary, #a0a0b0)" font-size="10" text-anchor="middle"
          font-family="var(--font-mono, monospace)"
          transform="rotate(-90, 12, {MARGIN.top + plotH() / 2})">Count</text>

    <!-- Bars -->
    <g clip-path="url(#hist-clip)">
      {#each counts as c, i}
        <rect
          x={MARGIN.left + i * barWidth}
          y={countToSvgY(c)}
          width={Math.max(barWidth - 1, 1)}
          height={MARGIN.top + plotH() - countToSvgY(c)}
          fill={hoveredBin === i ? 'var(--accent-primary, #5b6cf7)' : 'rgba(91, 108, 247, 0.6)'}
        />
      {/each}

      <!-- Gaussian overlay -->
      {#if gaussianPath}
        <path d={gaussianPath} fill="none" stroke="#ff6b35" stroke-width="1.5" opacity="0.8" />
      {/if}

      <!-- Mean line -->
      {#if stats.mean !== undefined}
        <line x1={dataToSvgX(stats.mean)} y1={MARGIN.top} x2={dataToSvgX(stats.mean)} y2={MARGIN.top + plotH()}
              stroke="#22d3ee" stroke-width="1" stroke-dasharray="4,3" />
      {/if}
      <!-- Median line -->
      {#if stats.median !== undefined}
        <line x1={dataToSvgX(stats.median)} y1={MARGIN.top} x2={dataToSvgX(stats.median)} y2={MARGIN.top + plotH()}
              stroke="#a78bfa" stroke-width="1" stroke-dasharray="4,3" />
      {/if}

      <!-- Selection highlight -->
      {#if rangeStart !== null && rangeEnd !== null}
        <rect x={selRectX} y={MARGIN.top} width={selRectW} height={plotH()}
              fill="rgba(255,107,53,0.15)" stroke="#ff6b35" stroke-width="1" />
      {/if}
    </g>

    <!-- Border -->
    <rect x={MARGIN.left} y={MARGIN.top} width={plotW()} height={plotH()} fill="none" stroke="var(--border-color, #2a2a3e)" />

    <!-- Hover tooltip -->
    {#if hoveredBin !== null && binEdges.length > hoveredBin + 1}
      <g pointer-events="none">
        <rect x={hoverX + 8} y={hoverY - 34} width="160" height="28" rx="3"
              fill="rgba(15,15,26,0.92)" stroke="var(--accent-primary, #5b6cf7)" />
        <text x={hoverX + 14} y={hoverY - 16}
              fill="var(--text-primary, #e0e0f0)" font-size="10"
              font-family="var(--font-mono, monospace)">
          [{binEdges[hoveredBin].toFixed(1)}, {binEdges[hoveredBin + 1].toFixed(1)}) = {counts[hoveredBin]}
        </text>
      </g>
    {/if}
  </svg>

  <!-- Stats summary -->
  <div class="hist-stats">
    <div class="stat-row">
      <span class="stat-label">Mean</span>
      <span class="stat-value mean-color">{fmtStat(stats.mean)}</span>
      <span class="stat-label">Median</span>
      <span class="stat-value median-color">{fmtStat(stats.median)}</span>
      <span class="stat-label">Std</span>
      <span class="stat-value">{fmtStat(stats.std)}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Min</span>
      <span class="stat-value">{fmtStat(stats.min)}</span>
      <span class="stat-label">Max</span>
      <span class="stat-value">{fmtStat(stats.max)}</span>
      <span class="stat-label">Skew</span>
      <span class="stat-value">{fmtStat(stats.skewness)}</span>
    </div>
  </div>

  <div class="hist-info">
    <span>{nValid} valid pts | {bins} bins</span>
    <span class="hist-hint">Drag: select range | Dbl-click: clear</span>
  </div>
</div>

<style>
  .histogram-container {
    position: relative;
    background: var(--bg-secondary, #1a1a2e);
    border: 1px solid var(--border-color, #2a2a3e);
    border-radius: var(--radius-md, 6px);
    overflow: hidden;
  }
  .hist-svg {
    display: block;
    cursor: col-resize;
    user-select: none;
  }
  .hist-loading {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(15, 15, 26, 0.7);
    color: var(--text-muted, #6a6a80);
    font-size: 12px;
    z-index: 5;
  }
  .hist-stats {
    padding: 4px 8px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    background: var(--bg-primary, #0f0f1a);
    border-top: 1px solid var(--border-color, #2a2a3e);
  }
  .stat-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 9px;
    font-family: var(--font-mono, monospace);
  }
  .stat-label {
    color: var(--text-muted, #6a6a80);
    min-width: 32px;
  }
  .stat-value {
    color: var(--text-primary, #e0e0f0);
    min-width: 48px;
  }
  .mean-color { color: #22d3ee; }
  .median-color { color: #a78bfa; }
  .hist-info {
    display: flex;
    justify-content: space-between;
    padding: 2px 8px;
    font-size: 9px;
    font-family: var(--font-mono, monospace);
    color: var(--text-muted, #6a6a80);
    background: var(--bg-primary, #0f0f1a);
    border-top: 1px solid var(--border-color, #2a2a3e);
  }
  .hist-hint { opacity: 0.6; }
</style>
