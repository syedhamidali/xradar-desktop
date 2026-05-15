<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { wsManager } from '../utils/websocket';
  import { connectionStatus } from '../stores/radarData';
  import { addToast } from '../stores/toasts';
  import CollapsiblePanel from './CollapsiblePanel.svelte';

  type Station = { id: string; arco: boolean };
  type L2File = { name: string; path: string; size: number };
  type ArcoScan = { key: string; path: string };

  const isConnected = $derived($connectionStatus === 'connected');

  // ── State ────────────────────────────────────────────────────────────────
  let mode = $state<'realtime' | 'archive' | 'arco'>('realtime');
  let stations = $state<Station[]>([]);
  let selectedStation = $state('KLOT');
  let loadingStations = $state(false);

  // Archive (Level II)
  let archiveDate = $state(todayStr());
  let l2Files = $state<L2File[]>([]);
  let loadingFiles = $state(false);

  // ARCO
  let arcoScans = $state<ArcoScan[]>([]);
  let loadingArco = $state(false);

  // Opening state
  let isOpening = $state(false);

  // ── Subscriptions ────────────────────────────────────────────────────────
  let unsubStations: (() => void) | null = null;
  let unsubFiles: (() => void) | null = null;
  let unsubArco: (() => void) | null = null;
  let unsubOpened: (() => void) | null = null;
  let unsubError: (() => void) | null = null;

  onMount(() => {
    unsubStations = wsManager.onMessage('nexrad_stations', (msg: any) => {
      stations = msg.stations ?? [];
      loadingStations = false;
    });
    unsubFiles = wsManager.onMessage('nexrad_l2_files', (msg: any) => {
      l2Files = msg.files ?? [];
      loadingFiles = false;
    });
    unsubArco = wsManager.onMessage('arco_scans', (msg: any) => {
      arcoScans = msg.scans ?? [];
      loadingArco = false;
    });
    unsubOpened = wsManager.onMessage('file_opened', () => {
      isOpening = false;
    });
    unsubError = wsManager.onMessage('error', () => {
      isOpening = false;
      loadingFiles = false;
      loadingArco = false;
    });

    // Auto-fetch station list once connected
    const unsubConn = connectionStatus.subscribe((s) => {
      if (s === 'connected' && stations.length === 0) {
        loadingStations = true;
        wsManager.send({ type: 'list_nexrad_stations' });
        unsubConn();
      }
    });
  });

  onDestroy(() => {
    unsubStations?.();
    unsubFiles?.();
    unsubArco?.();
    unsubOpened?.();
    unsubError?.();
  });

  // ── Helpers ──────────────────────────────────────────────────────────────
  function todayStr(): string {
    return new Date().toISOString().slice(0, 10);
  }

  function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  }

  // ── Actions ──────────────────────────────────────────────────────────────
  function fetchStations() {
    if (!isConnected) return;
    loadingStations = true;
    stations = [];
    wsManager.send({ type: 'list_nexrad_stations' });
  }

  function fetchL2Files() {
    if (!isConnected || !selectedStation || !archiveDate) return;
    loadingFiles = true;
    l2Files = [];
    wsManager.send({ type: 'list_nexrad_l2_files', station: selectedStation, date: archiveDate });
  }

  function fetchArcoScans() {
    if (!isConnected || !selectedStation) return;
    loadingArco = true;
    arcoScans = [];
    wsManager.send({ type: 'list_arco_scans', station: selectedStation, limit: 50 });
  }

  function openRealtime() {
    if (!isConnected || !selectedStation || isOpening) return;
    isOpening = true;
    addToast('info', `Fetching latest ${selectedStation} scan from AWS...`);
    wsManager.send({ type: 'open_nexrad_realtime', station: selectedStation });
  }

  function openL2File(file: L2File) {
    if (!isConnected || isOpening) return;
    isOpening = true;
    addToast('info', `Opening ${file.name}...`);
    wsManager.send({ type: 'open_nexrad_l2', path: file.path });
  }

  function openArcoScan(scan: ArcoScan) {
    if (!isConnected || isOpening) return;
    isOpening = true;
    addToast('info', `Opening ARCO scan ${scan.key}...`);
    wsManager.send({ type: 'open_arco_scan', station: selectedStation, scan_key: scan.key });
  }
</script>

<CollapsiblePanel title="Cloud Data (AWS)" icon="☁">
  <div class="cdp-root">
    <!-- Mode tabs -->
    <div class="cdp-tabs">
      <button class="cdp-tab" class:active={mode === 'realtime'} on:click={() => { mode = 'realtime'; }}>Real-time</button>
      <button class="cdp-tab" class:active={mode === 'archive'} on:click={() => { mode = 'archive'; }}>Archive</button>
      <button class="cdp-tab" class:active={mode === 'arco'} on:click={() => { mode = 'arco'; }}>ARCO</button>
    </div>

    <!-- Station row -->
    <div class="cdp-row">
      <label class="cdp-label">Station</label>
      <div class="cdp-station-row">
        {#if stations.length > 0}
          <select class="cdp-select" bind:value={selectedStation}>
            {#each stations as s}
              <option value={s.id}>{s.id}{s.arco ? ' ★' : ''}</option>
            {/each}
          </select>
        {:else}
          <input class="cdp-input" bind:value={selectedStation} placeholder="e.g. KLOT" maxlength="4" />
        {/if}
        <button class="cdp-btn" on:click={fetchStations} disabled={!isConnected || loadingStations} title="Load full station list">
          {loadingStations ? '…' : '⟳'}
        </button>
      </div>
    </div>

    <!-- Real-time mode -->
    {#if mode === 'realtime'}
      <div class="cdp-section">
        <p class="cdp-hint">Opens the most recent NEXRAD Level II scan from <code>noaa-nexrad-level2</code> on AWS S3.</p>
        <button class="cdp-btn primary full" on:click={openRealtime} disabled={!isConnected || isOpening || !selectedStation}>
          {isOpening ? 'Loading…' : `Open Latest ${selectedStation}`}
        </button>
      </div>
    {/if}

    <!-- Archive mode -->
    {#if mode === 'archive'}
      <div class="cdp-section">
        <div class="cdp-row">
          <label class="cdp-label">Date</label>
          <input type="date" class="cdp-input flex1" bind:value={archiveDate} />
        </div>
        <button class="cdp-btn full" on:click={fetchL2Files} disabled={!isConnected || loadingFiles || !selectedStation}>
          {loadingFiles ? 'Loading…' : 'List Files'}
        </button>

        {#if l2Files.length > 0}
          <div class="cdp-file-list">
            {#each l2Files as file}
              <button class="cdp-file-row" on:click={() => openL2File(file)} disabled={isOpening}>
                <span class="cdp-file-name">{file.name.slice(-19)}</span>
                <span class="cdp-file-size">{formatBytes(file.size)}</span>
              </button>
            {/each}
          </div>
        {:else if !loadingFiles}
          <p class="cdp-empty">No files. Click "List Files" to search.</p>
        {/if}
      </div>
    {/if}

    <!-- ARCO mode -->
    {#if mode === 'arco'}
      <div class="cdp-section">
        <p class="cdp-hint">
          <code>nexrad-arco</code> icechunk store. Each entry is a Volume Coverage Pattern
          (VCP-12, VCP-34…) containing all sweeps for every scan. Opens the latest available scan.
          Currently KLOT (Chicago) only.
        </p>
        <button class="cdp-btn full" on:click={fetchArcoScans} disabled={!isConnected || loadingArco || !selectedStation}>
          {loadingArco ? 'Loading…' : 'List VCPs'}
        </button>

        {#if arcoScans.length > 0}
          <div class="cdp-file-list">
            {#each arcoScans as scan}
              <button class="cdp-file-row" on:click={() => openArcoScan(scan)} disabled={isOpening}>
                <span class="cdp-file-name">{scan.key}</span>
                <span class="cdp-file-size">latest</span>
              </button>
            {/each}
          </div>
        {:else if !loadingArco}
          <p class="cdp-empty">No VCPs loaded. Click "List VCPs".</p>
        {/if}
      </div>
    {/if}
  </div>
</CollapsiblePanel>

<style>
  .cdp-root {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm, 6px);
  }

  .cdp-tabs {
    display: flex;
    border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
    border-radius: var(--radius-sm, 4px);
    overflow: hidden;
  }

  .cdp-tab {
    flex: 1;
    padding: 5px 0;
    font-size: 11px;
    font-weight: 600;
    background: transparent;
    color: var(--text-secondary, rgba(255,255,255,0.6));
    border: none;
    border-radius: 0;
    cursor: pointer;
    transition: all 150ms;
  }

  .cdp-tab:not(:last-child) {
    border-right: 1px solid var(--glass-border, rgba(255,255,255,0.08));
  }

  .cdp-tab.active {
    background: rgba(91, 108, 247, 0.15);
    color: var(--accent-hover, #7b8aff);
  }

  .cdp-row {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm, 6px);
  }

  .cdp-label {
    font-size: 11px;
    color: var(--text-muted, rgba(255,255,255,0.4));
    text-transform: uppercase;
    letter-spacing: 0.03em;
    font-weight: 600;
    min-width: 52px;
    flex-shrink: 0;
  }

  .cdp-station-row {
    display: flex;
    gap: 4px;
    flex: 1;
  }

  .cdp-select {
    flex: 1;
    min-width: 0;
  }

  .cdp-input {
    flex: 1;
    min-width: 0;
    padding: 3px 6px;
    font-size: 11px;
    font-family: var(--font-mono, monospace);
    background: var(--bg-primary, #0f0f1a);
    border: 1px solid var(--border-color, rgba(255,255,255,0.1));
    border-radius: var(--radius-sm, 4px);
    color: var(--text-primary, #fff);
  }

  .cdp-input.flex1 { flex: 1; }

  .cdp-section {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs, 4px);
  }

  .cdp-hint {
    font-size: 10px;
    color: var(--text-muted, rgba(255,255,255,0.35));
    line-height: 1.5;
    margin: 0;
  }

  .cdp-hint code {
    font-family: var(--font-mono, monospace);
    font-size: 10px;
    color: var(--accent-hover, #7b8aff);
  }

  .cdp-btn {
    padding: 5px 10px;
    font-size: 11px;
    font-weight: 600;
    border-radius: var(--radius-sm, 4px);
    border: 1px solid var(--glass-border, rgba(255,255,255,0.1));
    background: rgba(15, 15, 26, 0.6);
    color: var(--text-secondary, rgba(255,255,255,0.7));
    cursor: pointer;
    transition: all 150ms;
    white-space: nowrap;
  }

  .cdp-btn:hover:not(:disabled) {
    background: rgba(91, 108, 247, 0.1);
    color: var(--text-primary, #fff);
  }

  .cdp-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .cdp-btn.primary {
    background: rgba(91, 108, 247, 0.15);
    border-color: rgba(91, 108, 247, 0.3);
    color: var(--accent-hover, #7b8aff);
  }

  .cdp-btn.primary:hover:not(:disabled) {
    background: rgba(91, 108, 247, 0.3);
    color: #fff;
  }

  .cdp-btn.full { width: 100%; }

  .cdp-file-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 200px;
    overflow-y: auto;
    border: 1px solid var(--glass-border, rgba(255,255,255,0.06));
    border-radius: var(--radius-sm, 4px);
    padding: 2px;
  }

  .cdp-file-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 8px;
    font-size: 10px;
    font-family: var(--font-mono, monospace);
    color: var(--text-secondary, rgba(255,255,255,0.6));
    background: transparent;
    border: none;
    border-radius: 3px;
    cursor: pointer;
    text-align: left;
    transition: background 120ms;
    height: auto;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .cdp-file-row:hover:not(:disabled) {
    background: rgba(91, 108, 247, 0.08);
    color: var(--text-primary, #fff);
  }

  .cdp-file-row:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .cdp-file-name { flex: 1; }

  .cdp-file-size {
    color: var(--text-muted, rgba(255,255,255,0.3));
    font-size: 9px;
    margin-left: 6px;
  }

  .cdp-empty {
    font-size: 10px;
    color: var(--text-muted, rgba(255,255,255,0.3));
    text-align: center;
    padding: 8px 0;
    margin: 0;
  }
</style>
