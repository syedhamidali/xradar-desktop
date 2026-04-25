<script lang="ts">
  import { processingProgress, radarData, connectionStatus } from '../stores/radarData';
  import { wsManager } from '../utils/websocket';
  import CollapsiblePanel from './CollapsiblePanel.svelte';

  let despeckle = $state(false);
  let velocityDealiasing = $state(false);
  let griddingMethod = $state('none');

  // Retrievals state
  interface RetrievalDef {
    name: string;
    description: string;
    required_variables: string[];
    params: Record<string, { type: string; default: number; label: string }>;
  }

  let retrievals = $state<RetrievalDef[]>([]);
  let selectedRetrieval = $state<string>('');
  let retrievalParams = $state<Record<string, number>>({});
  let retrievalSweep = $state<number>(0);
  let retrievalRunning = $state(false);
  let retrievalResult = $state<{ name: string; units: string; long_name: string; stats: Record<string, number> } | null>(null);

  // Statistics state
  let statVariable = $state<string>('');
  let statSweep = $state<number>(0);
  let statType = $state<string>('histogram');
  let statResult = $state<Record<string, any> | null>(null);
  let statRunning = $state(false);

  const progress = $derived($processingProgress);
  const hasData = $derived($radarData.variables.length > 0);
  const isConnected = $derived($connectionStatus === 'connected');
  const canProcess = $derived(hasData && isConnected);
  const isProcessing = $derived(progress !== null && progress.percent < 100);
  const variables = $derived($radarData.variables);
  const sweeps = $derived($radarData.sweeps || []);

  // Set default variable/sweep when data changes
  $effect(() => {
    if (variables.length > 0 && !statVariable) {
      statVariable = variables[0];
    }
  });

  // Fetch retrievals list when connected and data loaded
  $effect(() => {
    if (canProcess && retrievals.length === 0) {
      fetchRetrievals();
    }
  });

  // Update retrieval params when selection changes
  $effect(() => {
    if (selectedRetrieval) {
      const def = retrievals.find(r => r.name === selectedRetrieval);
      if (def) {
        retrievalParams = {};
        for (const [key, spec] of Object.entries(def.params)) {
          retrievalParams[key] = spec.default;
        }
      }
    }
  });

  function fetchRetrievals() {
    wsManager.send({ type: 'list_retrievals' });
    const unsubscribe = wsManager.onMessage('retrievals_list', (msg: any) => {
      retrievals = msg.retrievals;
      if (retrievals.length > 0 && !selectedRetrieval) {
        selectedRetrieval = retrievals[0].name;
      }
      unsubscribe();
    });
  }

  function applyProcessing() {
    if (!canProcess) return;
    const pipeline: Record<string, any> = {
      despeckle,
      dealias: velocityDealiasing,
      gridding: griddingMethod,
    };
    wsManager.send({ type: 'process', pipeline });
  }

  function runRetrieval() {
    if (!canProcess || retrievalRunning || !selectedRetrieval) return;
    retrievalRunning = true;
    retrievalResult = null;

    const currentRetrieval = selectedRetrieval;
    wsManager.send({
      type: 'run_retrieval',
      name: currentRetrieval,
      sweep: retrievalSweep,
      params: retrievalParams,
    });

    const unsubResult = wsManager.onMessage('retrieval_result', (msg: any) => {
      if (msg.name === currentRetrieval) {
        retrievalResult = {
          name: msg.name,
          units: msg.units,
          long_name: msg.long_name,
          stats: msg.stats,
        };
        retrievalRunning = false;
        unsubResult();
        unsubErr();
      }
    });
    const unsubErr = wsManager.onMessage('error', () => {
      retrievalRunning = false;
      unsubResult();
      unsubErr();
    });
  }

  function runStatistics() {
    if (!canProcess || statRunning || !statVariable) return;
    statRunning = true;
    statResult = null;

    wsManager.send({
      type: 'get_statistics',
      variable: statVariable,
      sweep: statSweep,
      stat_type: statType,
    });

    const unsubStat = wsManager.onMessage('statistics_result', (msg: any) => {
      statResult = msg;
      statRunning = false;
      unsubStat();
      unsubStatErr();
    });
    const unsubStatErr = wsManager.onMessage('error', () => {
      statRunning = false;
      unsubStat();
      unsubStatErr();
    });
  }
</script>

<CollapsiblePanel title="Processing">
  {#if hasData}
    <div class="controls">
      <label class="checkbox-label" title="Remove isolated noisy pixels from radar data">
        <input type="checkbox" bind:checked={despeckle} disabled={!canProcess || isProcessing} />
        <span>Despeckle</span>
      </label>

      <label class="checkbox-label" title="Correct aliased velocity values">
        <input type="checkbox" bind:checked={velocityDealiasing} disabled={!canProcess || isProcessing} />
        <span>Velocity Dealiasing</span>
      </label>

      <div class="field">
        <label for="gridding-method">Gridding Method</label>
        <select id="gridding-method" bind:value={griddingMethod} disabled={!canProcess || isProcessing}>
          <option value="none">None</option>
          <option value="nearest">Nearest</option>
          <option value="linear">Linear</option>
          <option value="barnes">Barnes</option>
        </select>
      </div>

      <button
        class="primary action-btn"
        on:click={applyProcessing}
        disabled={!canProcess || isProcessing}
        title={!isConnected ? 'Sidecar not connected' : !hasData ? 'No data loaded' : 'Run processing pipeline'}
      >
        {#if isProcessing}
          <span class="spinner"></span>
          Processing...
        {:else}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          Apply Processing
        {/if}
      </button>
    </div>

    {#if progress}
      <div class="progress-section">
        <div class="progress-label">
          <span>{progress.message}</span>
          <span class="progress-pct">{progress.percent}%</span>
        </div>
        <div class="progress-bar">
          <div class="progress-bar-fill" style="width: {progress.percent}%"></div>
        </div>
      </div>
    {/if}
  {:else}
    <p class="no-data-msg">Load a file to enable processing</p>
  {/if}
</CollapsiblePanel>

<CollapsiblePanel title="Retrievals">
  {#if hasData && retrievals.length > 0}
    <div class="controls">
      <div class="field">
        <label for="retrieval-select">Algorithm</label>
        <select id="retrieval-select" bind:value={selectedRetrieval} disabled={!canProcess || retrievalRunning}>
          {#each retrievals as ret}
            <option value={ret.name}>{ret.description}</option>
          {/each}
        </select>
      </div>

      {#if selectedRetrieval}
        {@const def = retrievals.find(r => r.name === selectedRetrieval)}
        {#if def}
          {#if def.required_variables.length > 0}
            <div class="req-vars">
              <span class="req-label">Requires:</span>
              {#each def.required_variables as v}
                <span class="var-tag" class:available={variables.includes(v)} class:missing={!variables.includes(v)}>{v}</span>
              {/each}
            </div>
          {/if}

          {#each Object.entries(def.params) as [key, spec]}
            <div class="field param-field">
              <label for="param-{key}">{spec.label}</label>
              <input
                id="param-{key}"
                type="number"
                step="any"
                bind:value={retrievalParams[key]}
                disabled={!canProcess || retrievalRunning}
              />
            </div>
          {/each}
        {/if}
      {/if}

      <div class="field">
        <label for="retrieval-sweep">Sweep</label>
        <select id="retrieval-sweep" bind:value={retrievalSweep} disabled={!canProcess || retrievalRunning}>
          {#each sweeps as s, i}
            <option value={i}>Sweep {i}{s.elevation != null ? ` (${s.elevation.toFixed(1)}°)` : ''}</option>
          {/each}
        </select>
      </div>

      <button
        class="primary action-btn"
        on:click={runRetrieval}
        disabled={!canProcess || retrievalRunning}
      >
        {#if retrievalRunning}
          <span class="spinner"></span>
          Running...
        {:else}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          Run Retrieval
        {/if}
      </button>

      {#if retrievalResult}
        <div class="result-card">
          <div class="result-title">{retrievalResult.long_name}</div>
          <div class="result-units">{retrievalResult.units}</div>
          {#if retrievalResult.stats && Object.keys(retrievalResult.stats).length > 0}
            <div class="result-stats">
              {#if retrievalResult.stats.min != null}<span>Min: {retrievalResult.stats.min.toFixed(2)}</span>{/if}
              {#if retrievalResult.stats.max != null}<span>Max: {retrievalResult.stats.max.toFixed(2)}</span>{/if}
              {#if retrievalResult.stats.mean != null}<span>Mean: {retrievalResult.stats.mean.toFixed(2)}</span>{/if}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {:else if hasData}
    <p class="no-data-msg">Loading retrievals...</p>
  {:else}
    <p class="no-data-msg">Load a file to enable retrievals</p>
  {/if}
</CollapsiblePanel>

<CollapsiblePanel title="Statistics">
  {#if hasData}
    <div class="controls">
      <div class="field">
        <label for="stat-variable">Variable</label>
        <select id="stat-variable" bind:value={statVariable} disabled={!canProcess || statRunning}>
          {#each variables as v}
            <option value={v}>{v}</option>
          {/each}
        </select>
      </div>

      <div class="field">
        <label for="stat-sweep">Sweep</label>
        <select id="stat-sweep" bind:value={statSweep} disabled={!canProcess || statRunning}>
          {#each sweeps as s, i}
            <option value={i}>Sweep {i}{s.elevation != null ? ` (${s.elevation.toFixed(1)}°)` : ''}</option>
          {/each}
        </select>
      </div>

      <div class="field">
        <label for="stat-type">Statistic</label>
        <select id="stat-type" bind:value={statType} disabled={!canProcess || statRunning}>
          <option value="histogram">Histogram</option>
          <option value="profile">Vertical Profile</option>
          <option value="cell_stats">Cell Statistics</option>
          <option value="qc">QC Metrics</option>
        </select>
      </div>

      <button
        class="primary action-btn"
        on:click={runStatistics}
        disabled={!canProcess || statRunning}
      >
        {#if statRunning}
          <span class="spinner"></span>
          Computing...
        {:else}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
            <rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
          </svg>
          Compute
        {/if}
      </button>

      {#if statResult}
        <div class="result-card">
          {#if statType === 'histogram' && statResult.stats}
            <div class="result-title">Histogram: {statResult.variable}</div>
            <div class="result-stats">
              <span>Mean: {statResult.stats.mean?.toFixed(2)}</span>
              <span>Std: {statResult.stats.std?.toFixed(2)}</span>
              <span>Min: {statResult.stats.min?.toFixed(2)}</span>
              <span>Max: {statResult.stats.max?.toFixed(2)}</span>
              <span>Valid: {statResult.stats.count_valid?.toLocaleString()}</span>
            </div>
          {:else if statType === 'cell_stats'}
            <div class="result-title">Cell Statistics</div>
            <div class="result-stats">
              {#if statResult.max_reflectivity != null}<span>Max Z: {statResult.max_reflectivity.toFixed(1)} dBZ</span>{/if}
              {#if statResult.vil_estimate != null}<span>VIL: {statResult.vil_estimate} kg/m2</span>{/if}
              {#if statResult.echo_top_km != null}<span>Echo Top: {statResult.echo_top_km} km</span>{/if}
              <span>Sweeps: {statResult.n_sweeps}</span>
            </div>
          {:else if statType === 'qc'}
            <div class="result-title">QC: {statResult.variable}</div>
            <div class="result-stats">
              <span>Total gates: {statResult.total_gates?.toLocaleString()}</span>
              <span>Missing: {statResult.pct_missing}%</span>
              {#if statResult.pct_below_noise > 0}<span>Below noise: {statResult.pct_below_noise}%</span>{/if}
            </div>
          {:else if statType === 'profile'}
            <div class="result-title">Vertical Profile: {statResult.variable}</div>
            <div class="result-stats">
              {#each statResult.elevations as elev, i}
                <span>{elev.toFixed(1)}: {statResult.means[i] != null && !isNaN(statResult.means[i]) ? statResult.means[i].toFixed(2) : 'N/A'}</span>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {:else}
    <p class="no-data-msg">Load a file to enable statistics</p>
  {/if}
</CollapsiblePanel>

<style>
  .controls {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-md);
  }

  .checkbox-label {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    font-size: 12px;
    color: var(--text-primary);
    cursor: pointer;
    transition: opacity var(--transition-fast);
  }

  .checkbox-label:has(input:disabled) {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .checkbox-label input {
    margin: 0;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs);
  }

  .field label {
    font-size: 11px;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .field select,
  .field input[type="number"] {
    width: 100%;
  }

  .param-field input {
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 4px 6px;
  }

  .action-btn {
    margin-top: var(--spacing-xs);
    width: 100%;
  }

  .spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 600ms linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .progress-section {
    margin-top: var(--spacing-md);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs);
  }

  .progress-label {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--text-secondary);
  }

  .progress-pct {
    font-family: var(--font-mono);
    color: var(--text-accent);
  }

  .no-data-msg {
    font-size: 12px;
    color: var(--text-muted);
    text-align: center;
    padding: var(--spacing-md) 0;
  }

  .req-vars {
    display: flex;
    align-items: center;
    gap: var(--spacing-xs);
    flex-wrap: wrap;
    font-size: 11px;
  }

  .req-label {
    color: var(--text-secondary);
  }

  .var-tag {
    padding: 1px 6px;
    border-radius: 3px;
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .var-tag.available {
    background: rgba(76, 175, 80, 0.2);
    color: #4caf50;
  }

  .var-tag.missing {
    background: rgba(244, 67, 54, 0.2);
    color: #f44336;
  }

  .result-card {
    margin-top: var(--spacing-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    background: var(--bg-surface, rgba(255, 255, 255, 0.05));
    border-radius: 6px;
    border: 1px solid var(--border-subtle, rgba(255, 255, 255, 0.08));
  }

  .result-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 4px;
  }

  .result-units {
    font-size: 10px;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    margin-bottom: 4px;
  }

  .result-stats {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--text-secondary);
  }

  .result-stats span {
    display: flex;
    justify-content: space-between;
  }
</style>
