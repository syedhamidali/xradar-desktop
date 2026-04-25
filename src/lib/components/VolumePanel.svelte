<script lang="ts">
  /**
   * VolumePanel — ties together box selection, transfer function editing,
   * and 3D volume rendering. Placed in the right sidebar.
   *
   * Communicates with the PPI via events (box-select toggle) and listens
   * to the WebSocket for volume_data messages.
   */
  import { onMount, onDestroy } from 'svelte';
  import { wsManager } from '../utils/websocket';
  import { selectedVariable } from '../stores/radarData';
  import { addToast } from '../stores/toasts';
  import { boxSelectActive, selectedBox as selectedBoxStore } from '../stores/volumeSelect';
  import CollapsiblePanel from './CollapsiblePanel.svelte';
  import VolumeViewer from './VolumeViewer.svelte';
  import TransferFunctionEditor from './TransferFunctionEditor.svelte';

  // Volume data state
  let volumeData: Float32Array | null = $state(null);
  let nx = $state(0);
  let ny = $state(0);
  let nz = $state(0);
  let bounds = $state({ xMin: 0, xMax: 1, yMin: 0, yMax: 1, zMin: 0, zMax: 15000 });
  let vmin = $state(0);
  let vmax = $state(60);
  let isLoading = $state(false);

  // Settings
  let resolution: number = $state(100);
  let maxHeight: number = $state(15); // km

  // Transfer function
  let transferFunction = $state(createDefaultTransferFunction());

  // Store subscriptions
  const variable = $derived($selectedVariable ?? '');

  // Selected box (in meters from radar center)
  let selectedBox: { xMin: number; xMax: number; yMin: number; yMax: number } | null = $state(null);

  // Volume viewer ref
  let volumeViewer: VolumeViewer;

  // WebSocket listener
  let unsubVolume: (() => void) | null = null;

  onMount(() => {
    unsubVolume = wsManager.onMessage('volume_data', (msg: any) => {
      isLoading = false;

      if (msg.error) {
        addToast('error', `Volume error: ${msg.error}`);
        return;
      }

      // Decode volume data from the message
      const dataArray = msg.data;
      if (dataArray && Array.isArray(dataArray)) {
        volumeData = new Float32Array(dataArray);
      } else if (dataArray instanceof Float32Array) {
        volumeData = dataArray;
      } else if (dataArray instanceof ArrayBuffer) {
        volumeData = new Float32Array(dataArray);
      } else {
        volumeData = null;
      }

      nx = msg.nx ?? 0;
      ny = msg.ny ?? 0;
      nz = msg.nz ?? 0;
      bounds = {
        xMin: msg.bounds?.xMin ?? 0,
        xMax: msg.bounds?.xMax ?? 1,
        yMin: msg.bounds?.yMin ?? 0,
        yMax: msg.bounds?.yMax ?? 1,
        zMin: msg.bounds?.zMin ?? 0,
        zMax: msg.bounds?.zMax ?? maxHeight * 1000,
      };
      vmin = msg.vmin ?? 0;
      vmax = msg.vmax ?? 60;

      if (volumeData) {
        addToast('success', `Volume loaded: ${nx}x${ny}x${nz} (${variable})`);
      }
    });
  });

  onDestroy(() => {
    unsubVolume?.();
    unsubBox();
  });

  function toggleBoxSelect() {
    boxSelectActive.update(v => !v);
  }

  // React to box selections from the PPI
  const unsubBox = selectedBoxStore.subscribe((box) => {
    if (box) {
      selectedBox = box;
      boxSelectActive.set(false);
      requestVolume();
    }
  });

  function requestVolume() {
    if (!selectedBox || !variable) {
      addToast('info', 'Select a region on the PPI first');
      return;
    }

    isLoading = true;
    volumeData = null;

    wsManager.send({
      type: 'get_volume',
      variable,
      box: {
        x_min: selectedBox.xMin,
        x_max: selectedBox.xMax,
        y_min: selectedBox.yMin,
        y_max: selectedBox.yMax,
      },
      resolution,
      z_max: maxHeight * 1000,
    });
  }

  function onTransferFunctionChange(detail: { transferFunction: Uint8Array }) {
    transferFunction = detail.transferFunction;
    // If a renderer is active, it would pick up the new transfer function
    // through the VolumeViewer component binding
  }

  function createDefaultTransferFunction(): Uint8Array {
    const tf = new Uint8Array(256 * 4);
    // Default: grayscale ramp with linear alpha
    for (let i = 0; i < 256; i++) {
      tf[i * 4 + 0] = i;
      tf[i * 4 + 1] = i;
      tf[i * 4 + 2] = i;
      tf[i * 4 + 3] = i;
    }
    return tf;
  }

  const hasBox = $derived(selectedBox !== null);
  const hasVolume = $derived(volumeData !== null);
  const boxLabel = $derived.by(() => {
    const b = selectedBox;
    if (!b) return '';
    return `${(Math.abs(b.xMax - b.xMin) / 1000).toFixed(0)} x ${(Math.abs(b.yMax - b.yMin) / 1000).toFixed(0)} km`;
  });
</script>

<CollapsiblePanel title="3D Volume" icon="&#9634;">
  <div class="vp-root">
    <!-- Box select toggle -->
    <div class="vp-row">
      <button
        class="vp-btn"
        class:active={$boxSelectActive}
        on:click={toggleBoxSelect}
        title={$boxSelectActive ? 'Cancel box selection' : 'Draw a selection box on the PPI'}
      >
        {$boxSelectActive ? 'Drawing...' : 'Select Region'}
      </button>
      {#if hasBox}
        <span class="vp-box-label">{boxLabel}</span>
      {/if}
    </div>

    <!-- Settings row -->
    <div class="vp-settings">
      <div class="vp-field">
        <label class="vp-label" for="vp-resolution">Resolution</label>
        <select id="vp-resolution" class="vp-select" bind:value={resolution}>
          <option value={50}>50</option>
          <option value={75}>75</option>
          <option value={100}>100</option>
          <option value={150}>150</option>
        </select>
      </div>

      <div class="vp-field">
        <label class="vp-label" for="vp-max-height">Max Height (km)</label>
        <input
          id="vp-max-height"
          type="number"
          class="vp-input"
          bind:value={maxHeight}
          min="1"
          max="25"
          step="1"
        />
      </div>
    </div>

    <!-- Re-request button -->
    {#if hasBox}
      <button
        class="vp-btn primary"
        on:click={requestVolume}
        disabled={isLoading || !variable}
      >
        {#if isLoading}
          <span class="vp-spinner"></span>
          Loading...
        {:else}
          Render Volume
        {/if}
      </button>
    {/if}

    <!-- Transfer function editor -->
    <div class="vp-section">
      <span class="vp-section-title">Transfer Function</span>
      <TransferFunctionEditor
        bind:transferFunction={transferFunction}
        onchange={onTransferFunctionChange}
      />
    </div>

    <!-- Volume viewer -->
    <div class="vp-viewer">
      <VolumeViewer
        bind:this={volumeViewer}
        {volumeData}
        {nx} {ny} {nz}
        {bounds}
        {vmin} {vmax}
        {variable}
      />
    </div>
  </div>
</CollapsiblePanel>

<style>
  .vp-root {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm, 6px);
  }

  .vp-row {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm, 6px);
  }

  .vp-btn {
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
    border-radius: var(--radius-sm, 4px);
    border: 1px solid var(--glass-border, rgba(255,255,255,0.1));
    background: rgba(15, 15, 26, 0.6);
    color: var(--text-secondary, rgba(255,255,255,0.7));
    cursor: pointer;
    transition: all 150ms ease;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .vp-btn:hover:not(:disabled) {
    background: rgba(91, 108, 247, 0.1);
    border-color: rgba(91, 108, 247, 0.2);
    color: var(--text-primary, #fff);
  }

  .vp-btn.active {
    background: rgba(91, 108, 247, 0.2);
    border-color: rgba(91, 108, 247, 0.4);
    color: var(--accent-hover, #7b8aff);
  }

  .vp-btn.primary {
    background: rgba(91, 108, 247, 0.15);
    border-color: rgba(91, 108, 247, 0.3);
    color: var(--accent-hover, #7b8aff);
    width: 100%;
    justify-content: center;
  }

  .vp-btn.primary:hover:not(:disabled) {
    background: rgba(91, 108, 247, 0.3);
    color: white;
  }

  .vp-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .vp-box-label {
    font-size: 10px;
    font-family: var(--font-mono, monospace);
    color: var(--text-muted, rgba(255,255,255,0.4));
  }

  /* Settings */
  .vp-settings {
    display: flex;
    gap: var(--spacing-sm, 6px);
  }

  .vp-field {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
  }

  .vp-label {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted, rgba(255,255,255,0.4));
    text-transform: uppercase;
    letter-spacing: 0.03em;
    white-space: nowrap;
  }

  .vp-select {
    width: 100%;
    padding: 3px 6px;
    font-size: 11px;
    font-family: var(--font-mono, monospace);
    background: var(--bg-primary, #0f0f1a);
    border: 1px solid var(--border-color, rgba(255,255,255,0.1));
    border-radius: var(--radius-sm, 4px);
    color: var(--text-primary, #fff);
  }

  .vp-input {
    width: 100%;
    padding: 3px 6px;
    font-size: 11px;
    font-family: var(--font-mono, monospace);
    background: var(--bg-primary, #0f0f1a);
    border: 1px solid var(--border-color, rgba(255,255,255,0.1));
    border-radius: var(--radius-sm, 4px);
    color: var(--text-primary, #fff);
  }

  .vp-spinner {
    width: 12px;
    height: 12px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: vp-spin 600ms linear infinite;
    display: inline-block;
  }

  @keyframes vp-spin {
    to { transform: rotate(360deg); }
  }

  /* Section */
  .vp-section {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs, 4px);
    margin-top: var(--spacing-xs, 4px);
  }

  .vp-section-title {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted, rgba(255,255,255,0.4));
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  /* Viewer */
  .vp-viewer {
    height: 300px;
    margin-top: var(--spacing-xs, 4px);
    border-radius: var(--radius-sm, 4px);
    overflow: hidden;
  }
</style>
