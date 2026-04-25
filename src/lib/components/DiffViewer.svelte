<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { wsManager } from '../utils/websocket';
  import { openFiles, type FileEntry } from '../stores/fileManager';
  import { selectedVariable, selectedSweep } from '../stores/radarData';
  import {
    diffMode,
    diffBaseFileId,
    diffCompareFileId,
    type DiffMode,
  } from '../stores/temporal';

  // ── State ──────────────────────────────────────────────────────────────
  let canvasEl: HTMLCanvasElement;
  let containerEl: HTMLDivElement;
  let ctx: CanvasRenderingContext2D | null = null;
  let width = 600;
  let height = 600;
  let mode: DiffMode = 'off';
  let baseId: string | null = null;
  let compareId: string | null = null;
  let files: FileEntry[] = [];
  let variable: string | null = null;
  let sweep = 0;
  let diffRange = 50; // configurable max diff magnitude for colormap
  let isLoading = false;

  // Diff data received from server
  let diffHeader: any = null;
  let pendingBinaryChunks: Uint8Array[] = [];
  let pendingChunksExpected = 0;

  $: mode = $diffMode;
  $: baseId = $diffBaseFileId;
  $: compareId = $diffCompareFileId;
  $: files = $openFiles;
  $: variable = $selectedVariable;
  $: sweep = $selectedSweep;

  $: if (mode === 'diff' && baseId && compareId && variable) {
    requestDiff();
  }

  let unsubDiff: (() => void) | null = null;
  let unsubBinary: (() => void) | null = null;

  onMount(() => {
    if (canvasEl) {
      ctx = canvasEl.getContext('2d');
    }

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const s = Math.min(entry.contentRect.width, entry.contentRect.height);
        width = s;
        height = s;
      }
    });
    if (containerEl) ro.observe(containerEl);

    unsubDiff = wsManager.onMessage('difference_result', (msg: any) => {
      diffHeader = msg;
      pendingBinaryChunks = [];
      // Binary chunks follow; the WS manager dispatches them raw.
      // We need to intercept the next binary messages.
      pendingChunksExpected = 1; // Single combined buffer
      isLoading = false;
    });

    return () => {
      ro.disconnect();
      if (unsubDiff) unsubDiff();
    };
  });

  function requestDiff() {
    if (!baseId || !compareId || !variable) return;
    isLoading = true;
    wsManager.send({
      type: 'get_difference',
      variable,
      sweep,
      file_id_1: baseId,
      file_id_2: compareId,
    });
  }

  // ── Render diff on canvas when header arrives ──────────────────────────
  // The binary data is handled by the WebSocket manager's binary path.
  // We register a secondary handler to catch it.
  // For now, we rely on the header metadata and render a placeholder
  // until the binary arrives. The real rendering happens when we get
  // the sweep_data_ready equivalent for diff data.

  // Since the WS manager's binary handling is specific to sweep_data,
  // we hook into the raw message handler for diff binary data.

  $: if (diffHeader && ctx) {
    renderDiffFromHeader(diffHeader);
  }

  function renderDiffFromHeader(header: any) {
    if (!ctx || !canvasEl) return;

    canvasEl.width = width;
    canvasEl.height = height;
    ctx.clearRect(0, 0, width, height);

    const nAz = header.n_az ?? 0;
    const nRange = header.n_range ?? 0;
    const vmin = header.vmin ?? -diffRange;
    const vmax = header.vmax ?? diffRange;
    const maxRange = header.max_range ?? 1;

    if (nAz === 0 || nRange === 0) return;

    // Draw a "waiting for binary data" indicator
    ctx.fillStyle = 'rgba(8, 10, 20, 0.8)';
    ctx.fillRect(0, 0, width, height);

    // Draw diff info
    ctx.fillStyle = '#aaa';
    ctx.font = '12px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText(
      `Diff: ${header.variable} sweep ${header.sweep}`,
      width / 2,
      height / 2 - 20,
    );
    ctx.fillText(
      `${nAz} az x ${nRange} rng | range: [${vmin.toFixed(1)}, ${vmax.toFixed(1)}]`,
      width / 2,
      height / 2,
    );
    ctx.fillText('Binary data rendering in progress...', width / 2, height / 2 + 20);

    // Draw the diverging colorbar legend at bottom
    drawColorbar(ctx, width, height, vmin, vmax);
  }

  function drawColorbar(
    c: CanvasRenderingContext2D,
    w: number,
    h: number,
    vmin: number,
    vmax: number,
  ) {
    const barH = 12;
    const barW = w * 0.6;
    const x0 = (w - barW) / 2;
    const y0 = h - 40;

    for (let i = 0; i < barW; i++) {
      const t = i / barW; // 0..1
      const [r, g, b] = divergingColor(t);
      c.fillStyle = `rgb(${r},${g},${b})`;
      c.fillRect(x0 + i, y0, 1, barH);
    }

    // Border
    c.strokeStyle = 'rgba(255,255,255,0.2)';
    c.strokeRect(x0, y0, barW, barH);

    // Labels
    c.fillStyle = '#888';
    c.font = '10px monospace';
    c.textAlign = 'left';
    c.fillText(vmin.toFixed(1), x0, y0 + barH + 12);
    c.textAlign = 'center';
    c.fillText('0', x0 + barW / 2, y0 + barH + 12);
    c.textAlign = 'right';
    c.fillText(vmax.toFixed(1), x0 + barW, y0 + barH + 12);
  }

  /**
   * Diverging blue-white-red colormap.
   * t in [0, 1]: 0 = blue (decrease), 0.5 = white, 1 = red (increase).
   */
  function divergingColor(t: number): [number, number, number] {
    if (t < 0.5) {
      // Blue to white
      const s = t * 2; // 0..1
      const r = Math.round(30 + 225 * s);
      const g = Math.round(80 + 175 * s);
      const b = Math.round(220 + 35 * s);
      return [r, g, b];
    } else {
      // White to red
      const s = (t - 0.5) * 2; // 0..1
      const r = 255;
      const g = Math.round(255 - 195 * s);
      const b = Math.round(255 - 210 * s);
      return [r, g, b];
    }
  }

  function handleBaseChange(e: Event) {
    const val = (e.target as HTMLSelectElement).value;
    diffBaseFileId.set(val || null);
  }

  function handleCompareChange(e: Event) {
    const val = (e.target as HTMLSelectElement).value;
    diffCompareFileId.set(val || null);
  }

  function handleRangeChange(e: Event) {
    diffRange = parseFloat((e.target as HTMLInputElement).value) || 50;
  }
</script>

{#if mode === 'diff'}
  <div class="diff-viewer" bind:this={containerEl}>
    <div class="diff-controls">
      <div class="diff-select-group">
        <label class="diff-label">Base</label>
        <select class="diff-select" on:change={handleBaseChange} value={baseId ?? ''}>
          <option value="">-- select --</option>
          {#each files as f}
            <option value={f.id}>{f.filename}</option>
          {/each}
        </select>
      </div>

      <span class="diff-arrow">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M5 12h14m-7-7 7 7-7 7" />
        </svg>
      </span>

      <div class="diff-select-group">
        <label class="diff-label">Compare</label>
        <select class="diff-select" on:change={handleCompareChange} value={compareId ?? ''}>
          <option value="">-- select --</option>
          {#each files as f}
            <option value={f.id}>{f.filename}</option>
          {/each}
        </select>
      </div>

      <div class="diff-range-group">
        <label class="diff-label">Range</label>
        <input
          class="diff-range-input"
          type="number"
          value={diffRange}
          on:change={handleRangeChange}
          min="1"
          max="200"
          step="5"
        />
      </div>
    </div>

    <div class="diff-canvas-container">
      {#if isLoading}
        <div class="diff-loading">Computing difference...</div>
      {/if}
      <canvas bind:this={canvasEl} width={width} height={height} class="diff-canvas" />
    </div>

    <!-- Colorbar legend -->
    <div class="diff-legend">
      <div class="diff-legend-bar">
        <div class="diff-legend-gradient"></div>
      </div>
      <div class="diff-legend-labels">
        <span>-{diffRange} (decrease)</span>
        <span>0</span>
        <span>+{diffRange} (increase)</span>
      </div>
    </div>
  </div>
{/if}

<style>
  .diff-viewer {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    background: var(--bg-primary);
    gap: 8px;
  }

  .diff-controls {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    padding: 8px 12px;
    background: var(--glass-bg);
    border-bottom: 1px solid var(--glass-border);
    flex-wrap: wrap;
  }

  .diff-select-group {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 100px;
  }

  .diff-label {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    font-weight: 600;
  }

  .diff-select {
    font-size: 11px;
    padding: 4px 8px;
    background: rgba(8, 10, 20, 0.5);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    cursor: pointer;
    outline: none;
    transition: border-color var(--transition-fast);
  }

  .diff-select:hover,
  .diff-select:focus {
    border-color: var(--accent-primary);
  }

  .diff-arrow {
    color: var(--text-muted);
    padding-bottom: 4px;
  }

  .diff-range-group {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: 60px;
  }

  .diff-range-input {
    font-size: 11px;
    padding: 4px 6px;
    background: rgba(8, 10, 20, 0.5);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    width: 100%;
    text-align: center;
    outline: none;
  }

  .diff-range-input:focus {
    border-color: var(--accent-primary);
  }

  .diff-canvas-container {
    flex: 1;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 200px;
  }

  .diff-canvas {
    max-width: 100%;
    max-height: 100%;
    border-radius: var(--radius-sm);
  }

  .diff-loading {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    font-size: 12px;
    background: rgba(8, 10, 20, 0.6);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    z-index: 2;
    border-radius: var(--radius-sm);
  }

  .diff-legend {
    padding: 4px 12px 8px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
  }

  .diff-legend-bar {
    width: 80%;
    height: 10px;
    border-radius: 5px;
    overflow: hidden;
    border: 1px solid var(--glass-border);
  }

  .diff-legend-gradient {
    width: 100%;
    height: 100%;
    background: linear-gradient(
      to right,
      rgb(30, 80, 220),
      rgb(140, 170, 245),
      rgb(255, 255, 255),
      rgb(255, 140, 100),
      rgb(255, 60, 45)
    );
  }

  .diff-legend-labels {
    display: flex;
    justify-content: space-between;
    width: 80%;
    font-size: 9px;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }
</style>
