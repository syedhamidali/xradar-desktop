<script lang="ts">
  import { radarData, connectionStatus, selectedVariable, selectedSweep, processingProgress } from '../stores/radarData';
  import { wsManager } from '../utils/websocket';
  import CollapsiblePanel from './CollapsiblePanel.svelte';

  // --- Types ---
  interface QCParamSpec {
    type: string;
    default: any;
    label: string;
    min?: number;
    max?: number;
    options?: string[];
  }

  interface QCAlgorithm {
    name: string;
    description: string;
    params: Record<string, QCParamSpec>;
    applicable_variables: string[];
  }

  interface PipelineStep {
    name: string;
    params: Record<string, any>;
    expanded: boolean;
  }

  interface PipelinePreset {
    name: string;
    steps: { name: string; params: Record<string, any> }[];
  }

  // --- State ---
  let algorithms: QCAlgorithm[] = $state([]);
  let pipelineSteps: PipelineStep[] = $state([]);
  let isRunning = $state(false);
  let hasQCData = $state(false);
  let previewMode = $state(false);
  let presets: PipelinePreset[] = $state([]);
  let presetName = $state('');
  let dragIndex: number | null = $state(null);
  let dropIndex: number | null = $state(null);

  const progress = $derived($processingProgress);
  const hasData = $derived($radarData.variables.length > 0);
  const isConnected = $derived($connectionStatus === 'connected');
  const canRun = $derived(hasData && isConnected && pipelineSteps.length > 0 && !isRunning);
  const variables = $derived($radarData.variables);
  const sweeps = $derived($radarData.sweeps || []);
  const currentVariable = $derived($selectedVariable);
  const currentSweep = $derived($selectedSweep);

  // Fetch QC algorithms when connected and data loaded
  $effect(() => {
    if (hasData && isConnected && algorithms.length === 0) {
      fetchAlgorithms();
    }
  });

  // Load presets from localStorage
  $effect(() => {
    if (typeof window !== 'undefined') {
      try {
        const saved = localStorage.getItem('xradar-qc-presets');
        if (saved) presets = JSON.parse(saved);
      } catch { /* ignore */ }
    }
  });

  function fetchAlgorithms() {
    wsManager.send({ type: 'list_qc_algorithms' });
    const unsub = wsManager.onMessage('qc_algorithms_list', (msg: any) => {
      algorithms = msg.algorithms;
      unsub();
    });
  }

  function addStep(algoName: string) {
    const algo = algorithms.find(a => a.name === algoName);
    if (!algo) return;

    const params: Record<string, any> = {};
    for (const [key, spec] of Object.entries(algo.params)) {
      params[key] = spec.default;
    }
    pipelineSteps = [...pipelineSteps, { name: algoName, params, expanded: true }];
  }

  function removeStep(index: number) {
    pipelineSteps = pipelineSteps.filter((_, i) => i !== index);
  }

  function toggleExpand(index: number) {
    pipelineSteps = pipelineSteps.map((s, i) => i === index ? { ...s, expanded: !s.expanded } : s);
  }

  function moveStep(fromIdx: number, toIdx: number) {
    if (fromIdx === toIdx) return;
    const updated = [...pipelineSteps];
    const [item] = updated.splice(fromIdx, 1);
    updated.splice(toIdx, 0, item);
    pipelineSteps = updated;
  }

  // --- Drag and drop ---
  function onDragStart(e: DragEvent, index: number) {
    dragIndex = index;
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(index));
    }
  }

  function onDragOver(e: DragEvent, index: number) {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    dropIndex = index;
  }

  function onDragLeave() {
    dropIndex = null;
  }

  function onDrop(e: DragEvent, index: number) {
    e.preventDefault();
    if (dragIndex !== null && dragIndex !== index) {
      moveStep(dragIndex, index);
    }
    dragIndex = null;
    dropIndex = null;
  }

  function onDragEnd() {
    dragIndex = null;
    dropIndex = null;
  }

  // --- Run pipeline ---
  function runQC() {
    if (!canRun || !currentVariable) return;
    isRunning = true;
    hasQCData = false;

    const steps = pipelineSteps.map(s => ({
      name: s.name,
      params: { ...s.params },
    }));

    wsManager.send({
      type: 'run_qc',
      steps,
      variable: currentVariable,
      sweep: currentSweep,
    });

    const unsubData = wsManager.onMessage('qc_data_ready', () => {
      isRunning = false;
      hasQCData = true;
      unsubData();
      unsubErr();
    });
    const unsubErr = wsManager.onMessage('error', () => {
      isRunning = false;
      unsubData();
      unsubErr();
    });
  }

  // --- Undo: reload original data ---
  function undoQC() {
    if (!currentVariable) return;
    hasQCData = false;
    wsManager.requestSweepData(currentVariable, currentSweep);
  }

  // --- Preview toggle ---
  function togglePreview() {
    previewMode = !previewMode;
    if (previewMode && !hasQCData) {
      runQC();
    } else if (!previewMode && hasQCData) {
      undoQC();
    }
  }

  // --- Presets ---
  function savePreset() {
    if (!presetName.trim() || pipelineSteps.length === 0) return;
    const preset: PipelinePreset = {
      name: presetName.trim(),
      steps: pipelineSteps.map(s => ({ name: s.name, params: { ...s.params } })),
    };
    presets = [...presets.filter(p => p.name !== preset.name), preset];
    try { localStorage.setItem('xradar-qc-presets', JSON.stringify(presets)); } catch { /* ignore */ }
    presetName = '';
  }

  function loadPreset(preset: PipelinePreset) {
    pipelineSteps = preset.steps.map(s => ({ name: s.name, params: { ...s.params }, expanded: false }));
    hasQCData = false;
  }

  function deletePreset(name: string) {
    presets = presets.filter(p => p.name !== name);
    try { localStorage.setItem('xradar-qc-presets', JSON.stringify(presets)); } catch { /* ignore */ }
  }

  function getAlgoDescription(name: string): string {
    return algorithms.find(a => a.name === name)?.description ?? name;
  }

  function getAlgoParams(name: string): Record<string, QCParamSpec> {
    return algorithms.find(a => a.name === name)?.params ?? {};
  }
</script>

<CollapsiblePanel title="Quality Control" badge={pipelineSteps.length > 0 ? pipelineSteps.length : null}>
  {#if hasData}
    <div class="qc-panel">
      <!-- Algorithm picker -->
      <div class="section-label">Add QC Step</div>
      <div class="algo-list">
        {#each algorithms as algo}
          {@const applicable = algo.applicable_variables.some(v => variables.includes(v))}
          <button
            class="algo-btn"
            class:dimmed={!applicable}
            on:click={() => addStep(algo.name)}
            title={algo.description + (applicable ? '' : ' (no applicable variables loaded)')}
            disabled={isRunning}
          >
            <span class="algo-name">{algo.name.replace(/_/g, ' ')}</span>
            <svg class="algo-add-icon" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
          </button>
        {/each}
        {#if algorithms.length === 0}
          <div class="loading-msg">Loading algorithms...</div>
        {/if}
      </div>

      <!-- Pipeline builder -->
      {#if pipelineSteps.length > 0}
        <div class="section-label">Pipeline ({pipelineSteps.length} steps)</div>
        <div class="pipeline-list">
          {#each pipelineSteps as step, i (i)}
            <div
              class="pipeline-step"
              class:dragging={dragIndex === i}
              class:drop-target={dropIndex === i && dragIndex !== i}
              draggable="true"
              on:dragstart={(e) => onDragStart(e, i)}
              on:dragover={(e) => onDragOver(e, i)}
              on:dragleave={onDragLeave}
              on:drop={(e) => onDrop(e, i)}
              on:dragend={onDragEnd}
              role="listitem"
            >
              <div class="step-header">
                <span class="step-grip" title="Drag to reorder">
                  <svg width="8" height="12" viewBox="0 0 8 12" fill="currentColor">
                    <circle cx="2" cy="2" r="1.2"/><circle cx="6" cy="2" r="1.2"/>
                    <circle cx="2" cy="6" r="1.2"/><circle cx="6" cy="6" r="1.2"/>
                    <circle cx="2" cy="10" r="1.2"/><circle cx="6" cy="10" r="1.2"/>
                  </svg>
                </span>
                <span class="step-index">{i + 1}.</span>
                <button class="step-name-btn" on:click={() => toggleExpand(i)} title={getAlgoDescription(step.name)}>
                  {step.name.replace(/_/g, ' ')}
                </button>
                <button class="step-remove-btn" on:click={() => removeStep(i)} title="Remove step" disabled={isRunning}>
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                </button>
              </div>

              {#if step.expanded}
                <div class="step-params">
                  {#each Object.entries(getAlgoParams(step.name)) as [key, spec]}
                    <div class="param-row">
                      <label class="param-label" for="qc-param-{i}-{key}">{spec.label}</label>
                      {#if spec.options}
                        <select
                          id="qc-param-{i}-{key}"
                          bind:value={step.params[key]}
                          disabled={isRunning}
                          class="param-input"
                        >
                          {#each spec.options as opt}
                            <option value={opt}>{opt}</option>
                          {/each}
                        </select>
                      {:else if spec.type === 'float' || spec.type === 'int'}
                        <input
                          id="qc-param-{i}-{key}"
                          type="number"
                          step={spec.type === 'int' ? '1' : 'any'}
                          min={spec.min}
                          max={spec.max}
                          bind:value={step.params[key]}
                          disabled={isRunning}
                          class="param-input"
                        />
                      {:else}
                        <input
                          id="qc-param-{i}-{key}"
                          type="text"
                          bind:value={step.params[key]}
                          disabled={isRunning}
                          class="param-input"
                        />
                      {/if}
                    </div>
                  {/each}
                  {#if Object.keys(getAlgoParams(step.name)).length === 0}
                    <div class="no-params">No parameters</div>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        </div>

        <!-- Actions -->
        <div class="actions">
          <button
            class="primary action-btn"
            on:click={runQC}
            disabled={!canRun}
            title={!isConnected ? 'Not connected' : !hasData ? 'No data' : pipelineSteps.length === 0 ? 'Add steps first' : 'Run QC pipeline'}
          >
            {#if isRunning}
              <span class="spinner"></span>
              Processing...
            {:else}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
              Apply QC
            {/if}
          </button>

          <div class="action-row">
            <button
              class="secondary action-btn-sm"
              on:click={togglePreview}
              disabled={isRunning || pipelineSteps.length === 0}
              class:active={previewMode}
            >
              {previewMode ? 'Hide Preview' : 'Preview'}
            </button>

            <button
              class="secondary action-btn-sm"
              on:click={undoQC}
              disabled={!hasQCData || isRunning}
              title="Revert to original data"
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>
              </svg>
              Undo
            </button>
          </div>
        </div>

        <!-- Progress -->
        {#if isRunning && progress}
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

        {#if hasQCData && !isRunning}
          <div class="result-card">
            <div class="result-title">QC Applied</div>
            <div class="result-stats">
              {#each pipelineSteps as step, i}
                <span>{i + 1}. {step.name.replace(/_/g, ' ')}</span>
              {/each}
            </div>
          </div>
        {/if}
      {/if}

      <!-- Presets -->
      <div class="section-label preset-header">Presets</div>
      <div class="preset-section">
        {#if pipelineSteps.length > 0}
          <div class="preset-save-row">
            <input
              type="text"
              bind:value={presetName}
              placeholder="Preset name..."
              class="preset-name-input"
              on:keydown={(e) => e.key === 'Enter' && savePreset()}
            />
            <button class="preset-save-btn" on:click={savePreset} disabled={!presetName.trim()}>Save</button>
          </div>
        {/if}

        {#if presets.length > 0}
          <div class="preset-list">
            {#each presets as preset}
              <div class="preset-item">
                <button class="preset-load-btn" on:click={() => loadPreset(preset)} title="Load preset: {preset.name}">
                  <span class="preset-item-name">{preset.name}</span>
                  <span class="preset-item-count">{preset.steps.length} steps</span>
                </button>
                <button class="preset-delete-btn" on:click={() => deletePreset(preset.name)} title="Delete preset">
                  <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                </button>
              </div>
            {/each}
          </div>
        {:else}
          <div class="no-presets">No saved presets</div>
        {/if}
      </div>
    </div>
  {:else}
    <p class="no-data-msg">Load a file to enable quality control</p>
  {/if}
</CollapsiblePanel>

<style>
  .qc-panel {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
  }

  .section-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin-top: var(--spacing-xs);
  }

  /* Algorithm picker */
  .algo-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .algo-btn {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 5px 8px;
    font-size: 11px;
    color: var(--text-secondary);
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-sm, 4px);
    cursor: pointer;
    transition: all var(--transition-fast, 150ms);
    text-align: left;
    height: auto;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .algo-btn:hover:not(:disabled) {
    background: rgba(91, 108, 247, 0.06);
    border-color: var(--border-color, rgba(255, 255, 255, 0.08));
    color: var(--text-primary);
  }

  .algo-btn.dimmed {
    opacity: 0.45;
  }

  .algo-name {
    text-transform: capitalize;
  }

  .algo-add-icon {
    opacity: 0;
    transition: opacity var(--transition-fast, 150ms);
  }

  .algo-btn:hover .algo-add-icon {
    opacity: 0.6;
  }

  .loading-msg, .no-presets, .no-params {
    font-size: 11px;
    color: var(--text-muted);
    text-align: center;
    padding: var(--spacing-sm) 0;
  }

  /* Pipeline steps */
  .pipeline-list {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .pipeline-step {
    background: var(--bg-secondary, rgba(255, 255, 255, 0.03));
    border: 1px solid var(--border-color, rgba(255, 255, 255, 0.06));
    border-radius: var(--radius-sm, 4px);
    transition: all var(--transition-fast, 150ms);
    cursor: grab;
  }

  .pipeline-step.dragging {
    opacity: 0.4;
    border-color: var(--accent-primary, #5b6cf7);
  }

  .pipeline-step.drop-target {
    border-color: var(--accent-primary, #5b6cf7);
    box-shadow: 0 0 8px rgba(91, 108, 247, 0.15);
  }

  .step-header {
    display: flex;
    align-items: center;
    gap: var(--spacing-xs, 4px);
    padding: 5px 6px;
  }

  .step-grip {
    color: var(--text-muted);
    opacity: 0.4;
    cursor: grab;
    flex-shrink: 0;
    display: flex;
    align-items: center;
  }

  .step-index {
    font-size: 10px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    flex-shrink: 0;
    min-width: 16px;
  }

  .step-name-btn {
    flex: 1;
    font-size: 11px;
    font-weight: 500;
    color: var(--text-primary);
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    padding: 0;
    text-transform: capitalize;
    height: auto;
    border-radius: 0;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .step-name-btn:hover {
    color: var(--accent-primary, #5b6cf7);
  }

  .step-remove-btn {
    padding: 2px;
    color: var(--text-muted);
    background: transparent;
    border: none;
    cursor: pointer;
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    height: auto;
    width: auto;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .step-remove-btn:hover {
    color: var(--accent-danger, #f44336);
    background: rgba(244, 67, 54, 0.1);
  }

  .step-params {
    padding: 4px 8px 8px 28px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    border-top: 1px solid var(--border-color, rgba(255, 255, 255, 0.04));
  }

  .param-row {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm, 6px);
  }

  .param-label {
    font-size: 10px;
    color: var(--text-secondary);
    min-width: 80px;
    flex-shrink: 0;
  }

  .param-input {
    flex: 1;
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 3px 5px;
    min-width: 0;
  }

  /* Actions */
  .actions {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs, 4px);
    margin-top: var(--spacing-xs, 4px);
  }

  .action-row {
    display: flex;
    gap: var(--spacing-xs, 4px);
  }

  .action-btn {
    width: 100%;
  }

  .action-btn-sm {
    flex: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    font-size: 11px;
    padding: 5px 8px;
  }

  .action-btn-sm.active {
    background: var(--bg-active, rgba(91, 108, 247, 0.15));
    border-color: rgba(91, 108, 247, 0.3);
    color: var(--accent-primary, #5b6cf7);
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

  /* Progress */
  .progress-section {
    margin-top: var(--spacing-xs, 4px);
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .progress-label {
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: var(--text-secondary);
  }

  .progress-pct {
    font-family: var(--font-mono);
    color: var(--text-accent, var(--accent-primary));
  }

  .progress-bar {
    height: 3px;
    background: var(--bg-secondary, rgba(255, 255, 255, 0.05));
    border-radius: 2px;
    overflow: hidden;
  }

  .progress-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent-primary, #5b6cf7), var(--accent-hover, #7b8cff));
    transition: width 300ms ease;
    border-radius: 2px;
  }

  /* Result card */
  .result-card {
    margin-top: var(--spacing-xs, 4px);
    padding: var(--spacing-sm, 6px) var(--spacing-md, 10px);
    background: rgba(76, 175, 80, 0.06);
    border: 1px solid rgba(76, 175, 80, 0.15);
    border-radius: 6px;
  }

  .result-title {
    font-size: 11px;
    font-weight: 600;
    color: #4caf50;
    margin-bottom: 3px;
  }

  .result-stats {
    display: flex;
    flex-direction: column;
    gap: 1px;
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--text-secondary);
  }

  /* Presets */
  .preset-header {
    margin-top: var(--spacing-md, 12px);
  }

  .preset-section {
    display: flex;
    flex-direction: column;
    gap: var(--spacing-xs, 4px);
  }

  .preset-save-row {
    display: flex;
    gap: var(--spacing-xs, 4px);
  }

  .preset-name-input {
    flex: 1;
    font-size: 11px;
    padding: 4px 6px;
    min-width: 0;
  }

  .preset-save-btn {
    font-size: 10px;
    padding: 4px 10px;
    white-space: nowrap;
  }

  .preset-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .preset-item {
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .preset-load-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 8px;
    font-size: 11px;
    color: var(--text-secondary);
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-sm, 4px);
    cursor: pointer;
    text-align: left;
    height: auto;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .preset-load-btn:hover {
    background: rgba(91, 108, 247, 0.06);
    border-color: var(--border-color, rgba(255, 255, 255, 0.08));
    color: var(--text-primary);
  }

  .preset-item-name {
    font-weight: 500;
  }

  .preset-item-count {
    font-size: 9px;
    color: var(--text-muted);
    font-family: var(--font-mono);
  }

  .preset-delete-btn {
    padding: 3px;
    color: var(--text-muted);
    background: transparent;
    border: none;
    cursor: pointer;
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    height: auto;
    width: auto;
    opacity: 0.4;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .preset-delete-btn:hover {
    opacity: 1;
    color: var(--accent-danger, #f44336);
    background: rgba(244, 67, 54, 0.1);
  }

  .no-data-msg {
    font-size: 12px;
    color: var(--text-muted);
    text-align: center;
    padding: var(--spacing-md, 12px) 0;
  }
</style>
