<script lang="ts">
  /**
   * CrossSectionPanel -- Tabbed panel with Cross-section and Vertical Profile tabs.
   * Controls the line tool, probe mode, variable/colormap selectors, and results.
   */
  import { onMount, onDestroy } from 'svelte';
  import { connectionStatus, radarData, selectedVariable } from '../stores/radarData';
  import {
    crossSectionStore,
    type CrossSectionData,
    type VerticalProfileData,
    type CrossSectionLinePoints,
  } from '../stores/crossSection';
  import { wsManager } from '../utils/websocket';
  import { addToast } from '../stores/toasts';
  import { getDefaultCmap, getDefaultRange } from '../utils/ppiRenderer';
  import { COLORMAP_NAMES } from '../utils/colormaps';
  import CollapsiblePanel from './CollapsiblePanel.svelte';
  import CrossSectionViewer from './CrossSectionViewer.svelte';
  import VerticalProfile from './VerticalProfile.svelte';

  // Derived from stores
  const isConnected = $derived($connectionStatus === 'connected');
  const hasData = $derived($radarData.variables.length > 0);
  const variables = $derived($radarData.variables);
  const variable = $derived($selectedVariable);
  const activeTab = $derived($crossSectionStore.activeTab);
  const lineToolActive = $derived($crossSectionStore.lineToolActive);
  const probeToolActive = $derived($crossSectionStore.probeToolActive);
  const csData = $derived($crossSectionStore.crossSectionData);
  const vpProfiles = $derived($crossSectionStore.verticalProfiles);
  const isLoading = $derived($crossSectionStore.isLoading);

  // Cross-section state
  let csVariable = $state('');
  let csCmap = $state('turbo');
  let csVmin = $state(0);
  let csVmax = $state(60);

  // Vertical profile state
  let vpVariables = $state<string[]>([]);

  // Initialize CS variable to match selected variable
  $effect(() => {
    if (variable && !csVariable) {
      csVariable = variable;
      csCmap = getDefaultCmap(variable);
      const range = getDefaultRange(variable);
      if (range) { csVmin = range[0]; csVmax = range[1]; }
    }
  });

  // When CS variable changes, update colormap
  $effect(() => {
    if (csVariable) {
      csCmap = getDefaultCmap(csVariable);
      const range = getDefaultRange(csVariable);
      if (range) { csVmin = range[0]; csVmax = range[1]; }
    }
  });

  // VP multi-select: default to current variable
  $effect(() => {
    if (variable && vpVariables.length === 0) {
      vpVariables = [variable];
    }
  });

  // WebSocket message handlers
  let unsubCS: (() => void) | null = null;
  let unsubVP: (() => void) | null = null;

  onMount(() => {
    unsubCS = wsManager.onMessage('cross_section_result', (msg: any) => {
      const data: CrossSectionData = {
        distance_km: msg.distance_km ?? [],
        height_km: msg.height_km ?? [],
        values: msg.values ?? [],
        variable: msg.variable ?? '',
        units: msg.units ?? '',
        n_sweeps: msg.n_sweeps ?? 0,
      };
      crossSectionStore.update((s) => ({
        ...s,
        crossSectionData: data,
        isLoading: false,
      }));
      addToast('success', `Cross-section: ${data.n_sweeps} sweeps, ${data.distance_km.length} points`);
    });

    unsubVP = wsManager.onMessage('vertical_profile_result', (msg: any) => {
      const profile: VerticalProfileData = {
        heights_km: msg.heights_km ?? [],
        values: msg.values ?? [],
        elevations_deg: msg.elevations_deg ?? [],
        variable: msg.variable ?? '',
        units: msg.units ?? '',
      };
      crossSectionStore.update((s) => {
        // Replace existing profile for same variable, or add new
        const existing = s.verticalProfiles.filter((p) => p.variable !== profile.variable);
        return {
          ...s,
          verticalProfiles: [...existing, profile],
          isLoading: false,
        };
      });
      addToast('success', `Vertical profile: ${profile.heights_km.length} levels`);
    });
  });

  onDestroy(() => {
    unsubCS?.();
    unsubVP?.();
  });

  function setTab(tab: 'cross-section' | 'vertical-profile') {
    crossSectionStore.update((s) => ({ ...s, activeTab: tab }));
  }

  function toggleLineTool() {
    const next = !lineToolActive;
    crossSectionStore.update((s) => ({
      ...s,
      lineToolActive: next,
      probeToolActive: false,
    }));
  }

  function toggleProbeTool() {
    const next = !probeToolActive;
    crossSectionStore.update((s) => ({
      ...s,
      probeToolActive: next,
      lineToolActive: false,
    }));
  }

  function requestCrossSection() {
    const line = $crossSectionStore.line;
    if (!line) {
      addToast('info', 'Draw a line on the PPI first');
      return;
    }
    if (!csVariable) {
      addToast('info', 'Select a variable');
      return;
    }

    crossSectionStore.update((s) => ({ ...s, isLoading: true }));
    wsManager.send({
      type: 'get_cross_section',
      start: [line.startLat, line.startLon],
      end: [line.endLat, line.endLon],
      variable: csVariable,
      n_points: 200,
    });
  }

  function requestVerticalProfile(lat: number, lon: number) {
    if (vpVariables.length === 0) {
      addToast('info', 'Select at least one variable');
      return;
    }

    crossSectionStore.update((s) => ({ ...s, isLoading: true, verticalProfiles: [] }));

    for (const v of vpVariables) {
      wsManager.send({
        type: 'get_vertical_profile',
        lat,
        lon,
        variable: v,
      });
    }
  }

  function clearCrossSection() {
    crossSectionStore.update((s) => ({
      ...s,
      crossSectionData: null,
      line: null,
      lineToolActive: false,
    }));
  }

  function clearProfiles() {
    crossSectionStore.update((s) => ({
      ...s,
      verticalProfiles: [],
    }));
  }

  function toggleVpVariable(v: string) {
    if (vpVariables.includes(v)) {
      vpVariables = vpVariables.filter((x) => x !== v);
    } else {
      vpVariables = [...vpVariables, v];
    }
  }

  // Expose requestVerticalProfile so RadarViewer can call it on PPI click
  export { requestVerticalProfile };
</script>

<CollapsiblePanel title="Cross-Section / Profile" icon="///">
  <div class="csp-root">
    <!-- Tab bar -->
    <div class="csp-tabs">
      <button
        class="csp-tab"
        class:active={activeTab === 'cross-section'}
        on:click={() => setTab('cross-section')}
      >
        Cross-Section
      </button>
      <button
        class="csp-tab"
        class:active={activeTab === 'vertical-profile'}
        on:click={() => setTab('vertical-profile')}
      >
        Vertical Profile
      </button>
    </div>

    <!-- Cross-section tab -->
    {#if activeTab === 'cross-section'}
      <div class="csp-section">
        <div class="csp-controls">
          <div class="csp-row">
            <label class="csp-label">Variable</label>
            <select class="csp-select" bind:value={csVariable} disabled={!hasData}>
              {#each variables as v}
                <option value={v}>{v}</option>
              {/each}
            </select>
          </div>

          <div class="csp-row">
            <label class="csp-label">Colormap</label>
            <select class="csp-select" bind:value={csCmap}>
              {#each COLORMAP_NAMES as c}
                <option value={c}>{c}</option>
              {/each}
            </select>
          </div>

          <div class="csp-row">
            <label class="csp-label">Range</label>
            <div class="csp-range-inputs">
              <input type="number" class="csp-input" bind:value={csVmin} step="1" />
              <span class="csp-range-sep">-</span>
              <input type="number" class="csp-input" bind:value={csVmax} step="1" />
            </div>
          </div>
        </div>

        <div class="csp-actions">
          <button
            class="csp-btn"
            class:active={lineToolActive}
            on:click={toggleLineTool}
            disabled={!hasData || !isConnected}
            title="Draw a line on the PPI to define the cross-section"
          >
            {lineToolActive ? 'Drawing...' : 'Draw Line'}
          </button>

          <button
            class="csp-btn primary"
            on:click={requestCrossSection}
            disabled={!$crossSectionStore.line || !isConnected || isLoading}
          >
            {isLoading ? 'Loading...' : 'Request'}
          </button>

          {#if csData}
            <button class="csp-btn muted" on:click={clearCrossSection}>Clear</button>
          {/if}
        </div>

        <!-- Viewer -->
        <div class="csp-viewer">
          <CrossSectionViewer
            data={csData}
            cmapName={csCmap}
            vmin={csVmin}
            vmax={csVmax}
          />
        </div>
      </div>
    {/if}

    <!-- Vertical profile tab -->
    {#if activeTab === 'vertical-profile'}
      <div class="csp-section">
        <div class="csp-controls">
          <div class="csp-row">
            <label class="csp-label">Variables</label>
            <div class="csp-var-chips">
              {#each variables as v}
                <button
                  class="csp-chip"
                  class:selected={vpVariables.includes(v)}
                  on:click={() => toggleVpVariable(v)}
                >
                  {v}
                </button>
              {/each}
            </div>
          </div>
        </div>

        <div class="csp-actions">
          <button
            class="csp-btn"
            class:active={probeToolActive}
            on:click={toggleProbeTool}
            disabled={!hasData || !isConnected}
            title="Click on the PPI to get a vertical profile"
          >
            {probeToolActive ? 'Click PPI...' : 'Probe Mode'}
          </button>

          {#if vpProfiles.length > 0}
            <button class="csp-btn muted" on:click={clearProfiles}>Clear</button>
          {/if}
        </div>

        <!-- Viewer -->
        <div class="csp-viewer">
          <VerticalProfile profiles={vpProfiles} />
        </div>
      </div>
    {/if}
  </div>
</CollapsiblePanel>

<style>
  .csp-root {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm, 6px);
  }

  .csp-tabs {
    display: flex;
    border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
    border-radius: var(--radius-sm, 4px);
    overflow: hidden;
  }

  .csp-tab {
    flex: 1;
    padding: 6px 0;
    font-size: 11px;
    font-weight: 600;
    text-align: center;
    background: transparent;
    color: var(--text-secondary, rgba(255,255,255,0.6));
    border: none;
    border-radius: 0;
    cursor: pointer;
    transition: all 150ms ease;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
    height: auto;
  }

  .csp-tab:not(:last-child) {
    border-right: 1px solid var(--glass-border, rgba(255,255,255,0.08));
  }

  .csp-tab:hover {
    background: rgba(91, 108, 247, 0.06);
    color: var(--text-primary, #fff);
  }

  .csp-tab.active {
    background: rgba(91, 108, 247, 0.15);
    color: var(--accent-hover, #7b8aff);
  }

  .csp-section {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm, 6px);
  }

  .csp-controls {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs, 4px);
  }

  .csp-row {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm, 6px);
  }

  .csp-label {
    font-size: 11px;
    color: var(--text-muted, rgba(255,255,255,0.4));
    text-transform: uppercase;
    letter-spacing: 0.03em;
    font-weight: 600;
    min-width: 60px;
    flex-shrink: 0;
  }

  .csp-select {
    flex: 1;
    min-width: 0;
  }

  .csp-range-inputs {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
  }

  .csp-input {
    width: 60px;
    padding: 3px 6px;
    font-size: 11px;
    font-family: var(--font-mono, monospace);
    background: var(--bg-primary, #0f0f1a);
    border: 1px solid var(--border-color, rgba(255,255,255,0.1));
    border-radius: var(--radius-sm, 4px);
    color: var(--text-primary, #fff);
  }

  .csp-range-sep {
    color: var(--text-muted, rgba(255,255,255,0.3));
    font-size: 11px;
  }

  .csp-actions {
    display: flex;
    gap: var(--spacing-xs, 4px);
    flex-wrap: wrap;
  }

  .csp-btn {
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
  }

  .csp-btn:hover:not(:disabled) {
    background: rgba(91, 108, 247, 0.1);
    border-color: rgba(91, 108, 247, 0.2);
    color: var(--text-primary, #fff);
  }

  .csp-btn.active {
    background: rgba(91, 108, 247, 0.2);
    border-color: rgba(91, 108, 247, 0.4);
    color: var(--accent-hover, #7b8aff);
  }

  .csp-btn.primary {
    background: rgba(91, 108, 247, 0.15);
    border-color: rgba(91, 108, 247, 0.3);
    color: var(--accent-hover, #7b8aff);
  }

  .csp-btn.primary:hover:not(:disabled) {
    background: rgba(91, 108, 247, 0.3);
    color: white;
  }

  .csp-btn.muted {
    color: var(--text-muted, rgba(255,255,255,0.35));
    border-color: rgba(255,255,255,0.06);
  }

  .csp-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .csp-var-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
    flex: 1;
  }

  .csp-chip {
    padding: 2px 8px;
    font-size: 10px;
    font-family: var(--font-mono, monospace);
    font-weight: 600;
    border: 1px solid var(--border-color, rgba(255,255,255,0.1));
    border-radius: 10px;
    background: transparent;
    color: var(--text-muted, rgba(255,255,255,0.4));
    cursor: pointer;
    transition: all 150ms ease;
    height: auto;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .csp-chip:hover {
    border-color: rgba(91, 108, 247, 0.3);
    color: var(--text-secondary, rgba(255,255,255,0.7));
  }

  .csp-chip.selected {
    background: rgba(91, 108, 247, 0.15);
    border-color: rgba(91, 108, 247, 0.4);
    color: var(--accent-hover, #7b8aff);
  }

  .csp-viewer {
    height: 250px;
    border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
    border-radius: var(--radius-sm, 4px);
    overflow: hidden;
  }
</style>
