<script lang="ts">
  /**
   * StormPanel — control panel for storm cell identification and tracking.
   *
   * Provides threshold controls, an "Identify Cells" button, a results table,
   * and cell selection for highlighting on the PPI.
   */
  import { connectionStatus, radarData, selectedVariable, selectedSweep } from '../stores/radarData';
  import { stormCells, type CellData } from '../stores/stormCells';
  import { wsManager } from '../utils/websocket';
  import { addToast } from '../stores/toasts';
  import { onMount, onDestroy } from 'svelte';
  import CollapsiblePanel from './CollapsiblePanel.svelte';

  // Threshold controls
  let thresholdDbz: number = 35;
  let minAreaKm2: number = 10;

  // State
  let cells: CellData[] = [];
  let selectedId: string | null = null;
  let overlayVisible: boolean = true;
  let isIdentifying: boolean = false;
  let hasData: boolean = false;
  let isConnected: boolean = false;
  let variable: string | null = null;
  let sweep: number = 0;

  $: cells = $stormCells.cells;
  $: selectedId = $stormCells.selectedCellId;
  $: overlayVisible = $stormCells.overlayVisible;
  $: isIdentifying = $stormCells.isIdentifying;
  $: hasData = $radarData.variables.length > 0;
  $: isConnected = $connectionStatus === 'connected';
  $: variable = $selectedVariable;
  $: sweep = $selectedSweep;
  $: canIdentify = hasData && isConnected && !isIdentifying;

  // WebSocket message handlers
  let unsubCells: (() => void) | null = null;
  let unsubTracks: (() => void) | null = null;

  onMount(() => {
    unsubCells = wsManager.onMessage('cells_identified', (msg: any) => {
      const cellList: CellData[] = msg.cells ?? [];
      stormCells.update((s) => ({
        ...s,
        cells: cellList,
        tracks: [],
        selectedCellId: null,
        isIdentifying: false,
      }));
      if (cellList.length > 0) {
        addToast('success', `Found ${cellList.length} storm cells`);
      } else {
        addToast('info', 'No storm cells found with current thresholds');
      }
    });

    unsubTracks = wsManager.onMessage('cells_tracked', (msg: any) => {
      stormCells.update((s) => ({
        ...s,
        cells: msg.cells ?? [],
        tracks: msg.tracks ?? [],
        isIdentifying: false,
      }));
      addToast('success', `Tracked ${(msg.tracks ?? []).length} cells`);
    });
  });

  onDestroy(() => {
    unsubCells?.();
    unsubTracks?.();
  });

  function identifyCells() {
    if (!canIdentify || !variable) return;
    stormCells.update((s) => ({ ...s, isIdentifying: true }));
    wsManager.send({
      type: 'identify_cells',
      variable,
      sweep,
      params: {
        threshold_dbz: thresholdDbz,
        min_size_km2: minAreaKm2,
      },
    });
  }

  function trackCells() {
    if (!canIdentify || !variable || cells.length === 0) return;
    stormCells.update((s) => ({ ...s, isIdentifying: true }));
    wsManager.send({
      type: 'track_cells',
      variable,
      sweep,
      params: {
        threshold_dbz: thresholdDbz,
        min_size_km2: minAreaKm2,
        max_distance_km: 30,
        dt_seconds: 300,
      },
    });
  }

  function toggleOverlay() {
    stormCells.update((s) => ({ ...s, overlayVisible: !s.overlayVisible }));
  }

  function selectCell(id: string) {
    stormCells.update((s) => ({
      ...s,
      selectedCellId: s.selectedCellId === id ? null : id,
    }));
  }

  function clearCells() {
    stormCells.update((s) => ({
      ...s,
      cells: [],
      tracks: [],
      selectedCellId: null,
    }));
  }
</script>

<CollapsiblePanel title="Storm Cells" badge={cells.length > 0 ? cells.length : null}>
  {#if hasData}
    <div class="storm-controls">
      <!-- Threshold inputs -->
      <div class="field">
        <label for="threshold-dbz">Min dBZ</label>
        <input
          id="threshold-dbz"
          type="number"
          bind:value={thresholdDbz}
          min="10"
          max="70"
          step="5"
          disabled={!canIdentify}
        />
      </div>

      <div class="field">
        <label for="min-area">Min Area (km<sup>2</sup>)</label>
        <input
          id="min-area"
          type="number"
          bind:value={minAreaKm2}
          min="1"
          max="100"
          step="5"
          disabled={!canIdentify}
        />
      </div>

      <!-- Action buttons -->
      <div class="button-row">
        <button
          class="primary action-btn"
          on:click={identifyCells}
          disabled={!canIdentify}
          title={!isConnected ? 'Sidecar not connected' : !hasData ? 'No data loaded' : 'Find storm cells'}
        >
          {#if isIdentifying}
            <span class="spinner"></span>
            Identifying...
          {:else}
            Identify Cells
          {/if}
        </button>

        {#if cells.length > 0}
          <button
            class="action-btn secondary"
            on:click={trackCells}
            disabled={!canIdentify}
            title="Track cells to current sweep"
          >
            Track
          </button>
        {/if}
      </div>

      <!-- Overlay toggle + clear -->
      {#if cells.length > 0}
        <div class="toggle-row">
          <label class="checkbox-label">
            <input type="checkbox" checked={overlayVisible} on:change={toggleOverlay} />
            <span>Show overlay</span>
          </label>
          <button class="clear-btn" on:click={clearCells} title="Clear all cells">
            Clear
          </button>
        </div>
      {/if}
    </div>

    <!-- Results table -->
    {#if cells.length > 0}
      <div class="cells-table-wrapper">
        <table class="cells-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Max dBZ</th>
              <th>Mean</th>
              <th>Area km<sup>2</sup></th>
              <th>Range km</th>
            </tr>
          </thead>
          <tbody>
            {#each cells as cell (cell.id)}
              <!-- svelte-ignore a11y_click_events_have_key_events -->
              <tr
                class="cell-row"
                class:selected={cell.id === selectedId}
                on:click={() => selectCell(cell.id)}
                role="button"
                tabindex="0"
              >
                <td class="cell-id-col">{cell.id}</td>
                <td class="mono">{cell.max_dbz.toFixed(1)}</td>
                <td class="mono">{cell.mean_dbz.toFixed(1)}</td>
                <td class="mono">{cell.area_km2.toFixed(1)}</td>
                <td class="mono">{(cell.centroid_range / 1000).toFixed(1)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    <!-- Selected cell detail -->
    {#if selectedId}
      {@const selected = cells.find((c) => c.id === selectedId)}
      {#if selected}
        <div class="cell-detail">
          <div class="detail-header">{selected.id} Detail</div>
          <div class="detail-grid">
            <span class="detail-label">Max dBZ</span>
            <span class="detail-value">{selected.max_dbz.toFixed(1)}</span>
            <span class="detail-label">Mean dBZ</span>
            <span class="detail-value">{selected.mean_dbz.toFixed(1)}</span>
            <span class="detail-label">Area</span>
            <span class="detail-value">{selected.area_km2.toFixed(1)} km<sup>2</sup></span>
            <span class="detail-label">Centroid Az</span>
            <span class="detail-value">{selected.centroid_az.toFixed(1)}&deg;</span>
            <span class="detail-label">Centroid Range</span>
            <span class="detail-value">{(selected.centroid_range / 1000).toFixed(1)} km</span>
            <span class="detail-label">Boundary pts</span>
            <span class="detail-value">{selected.boundary_points.length}</span>
          </div>
        </div>
      {/if}
    {/if}
  {:else}
    <p class="empty-hint">Open a radar file to identify storm cells</p>
  {/if}
</CollapsiblePanel>

<style>
  .storm-controls {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm, 6px);
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .field label {
    font-size: 11px;
    color: var(--text-muted, #6b6b80);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .field input[type='number'] {
    width: 100%;
    padding: 4px 8px;
    font-family: var(--font-mono, monospace);
    font-size: 12px;
    background: var(--bg-primary, #0f0f1a);
    border: 1px solid var(--border-color, #2a2a3a);
    border-radius: var(--radius-sm, 4px);
    color: var(--text-primary, #e8e8ee);
  }

  .field input:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .button-row {
    display: flex;
    gap: var(--spacing-sm, 6px);
    margin-top: var(--spacing-xs, 4px);
  }

  .action-btn {
    flex: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    border-radius: var(--radius-sm, 4px);
    cursor: pointer;
    transition: background 150ms ease, opacity 150ms ease;
    border: 1px solid var(--border-color, #2a2a3a);
    color: var(--text-primary, #e8e8ee);
    background: var(--bg-surface, #1a1a2e);
  }

  .action-btn.primary {
    background: var(--accent-primary, #5b6cf7);
    border-color: var(--accent-primary, #5b6cf7);
    color: #fff;
  }

  .action-btn.primary:hover:not(:disabled) {
    background: var(--accent-hover, #7080ff);
  }

  .action-btn.secondary:hover:not(:disabled) {
    background: var(--bg-hover, #24243a);
  }

  .action-btn:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 600ms linear infinite;
    display: inline-block;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .toggle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: var(--spacing-xs, 4px);
  }

  .checkbox-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-secondary, #a0a0b8);
    cursor: pointer;
  }

  .clear-btn {
    padding: 2px 8px;
    font-size: 11px;
    color: var(--text-muted, #6b6b80);
    background: transparent;
    border: 1px solid var(--border-color, #2a2a3a);
    border-radius: 3px;
    cursor: pointer;
    transition: color 150ms ease, border-color 150ms ease;
  }

  .clear-btn:hover {
    color: var(--accent-danger, #ff4060);
    border-color: var(--accent-danger, #ff4060);
  }

  /* Table */
  .cells-table-wrapper {
    margin-top: var(--spacing-sm, 6px);
    max-height: 200px;
    overflow-y: auto;
    border: 1px solid var(--border-color, #2a2a3a);
    border-radius: var(--radius-sm, 4px);
  }

  .cells-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
  }

  .cells-table th {
    position: sticky;
    top: 0;
    padding: 4px 6px;
    text-align: left;
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted, #6b6b80);
    background: var(--bg-tertiary, #161625);
    border-bottom: 1px solid var(--border-color, #2a2a3a);
    white-space: nowrap;
  }

  .cells-table td {
    padding: 3px 6px;
    border-bottom: 1px solid var(--border-color, #2a2a3a);
    color: var(--text-secondary, #a0a0b8);
  }

  .cell-row {
    cursor: pointer;
    transition: background 100ms ease;
  }

  .cell-row:hover {
    background: var(--bg-hover, #24243a);
  }

  .cell-row.selected {
    background: var(--bg-active, #1e1e40);
  }

  .cell-row.selected td {
    color: var(--text-primary, #e8e8ee);
  }

  .cell-id-col {
    font-weight: 700;
    color: var(--accent-primary, #5b6cf7) !important;
  }

  .mono {
    font-family: var(--font-mono, monospace);
    font-variant-numeric: tabular-nums;
  }

  /* Detail card */
  .cell-detail {
    margin-top: var(--spacing-sm, 6px);
    padding: var(--spacing-sm, 6px);
    border: 1px solid var(--border-color, #2a2a3a);
    border-radius: var(--radius-sm, 4px);
    background: var(--bg-primary, #0f0f1a);
  }

  .detail-header {
    font-size: 11px;
    font-weight: 700;
    color: var(--accent-primary, #5b6cf7);
    margin-bottom: var(--spacing-xs, 4px);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .detail-grid {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 2px 8px;
    font-size: 11px;
  }

  .detail-label {
    color: var(--text-muted, #6b6b80);
    white-space: nowrap;
  }

  .detail-value {
    font-family: var(--font-mono, monospace);
    color: var(--text-primary, #e8e8ee);
    text-align: right;
  }

  .empty-hint {
    font-size: 12px;
    color: var(--text-muted, #6b6b80);
    text-align: center;
    padding: var(--spacing-md, 12px) 0;
  }
</style>
