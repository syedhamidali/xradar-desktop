<script lang="ts">
  import { radarData, selectedVariable, selectedSweep } from '../stores/radarData';
  import CollapsiblePanel from './CollapsiblePanel.svelte';
  import DataTable from './DataTable.svelte';
  import MetadataViewer from './MetadataViewer.svelte';

  const data = $derived($radarData);
  const currentVariable = $derived($selectedVariable);
  const hasData = $derived(data.variables.length > 0);

  // Variable search/filter
  let varSearch = $state('');
  const filteredVariables = $derived(varSearch.trim()
    ? data.variables.filter(v => v.toLowerCase().includes(varSearch.toLowerCase()))
    : data.variables);

  // Viewer visibility
  let showDataTable = $state(false);
  let showMetadata = $state(false);

  function selectVariable(v: string) {
    selectedVariable.set(v);
  }

  function selectSweep(s: number) {
    selectedSweep.set(s);
  }

  function formatValue(value: any): string {
    if (value === null || value === undefined) return 'null';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function truncate(s: string, max: number = 60): string {
    return s.length > max ? s.substring(0, max) + '...' : s;
  }

  function openDataTable() {
    showDataTable = true;
  }

  function openMetadata() {
    showMetadata = true;
  }
</script>

<div class="data-inspector">
  {#if hasData}
    <!-- Action toolbar -->
    <div class="inspector-toolbar">
      <button class="toolbar-btn" on:click={openDataTable} title="Open data table viewer">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <line x1="3" y1="9" x2="21" y2="9" />
          <line x1="3" y1="15" x2="21" y2="15" />
          <line x1="9" y1="3" x2="9" y2="21" />
        </svg>
        <span>Table View</span>
      </button>
      <button class="toolbar-btn" on:click={openMetadata} title="Open metadata tree viewer">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />
        </svg>
        <span>Metadata</span>
      </button>
    </div>

    <CollapsiblePanel title="Variables" badge={data.variables.length}>
      {#if data.variables.length > 5}
        <div class="var-search-wrapper">
          <input
            type="text"
            placeholder="Filter variables..."
            bind:value={varSearch}
            class="var-search-input"
          />
        </div>
      {/if}
      <ul class="var-list">
        {#each filteredVariables as v}
          <li>
            <button
              class="var-item"
              class:active={currentVariable === v}
              on:click={() => selectVariable(v)}
              title="Select variable: {v}"
            >
              <span class="var-icon">V</span>
              <span class="var-name">{v}</span>
            </button>
          </li>
        {/each}
        {#if filteredVariables.length === 0 && varSearch}
          <li class="text-muted empty-row" style="padding: 4px 8px; font-size: 11px;">No matching variables</li>
        {/if}
      </ul>
    </CollapsiblePanel>

    <CollapsiblePanel title="Dimensions" badge={Object.keys(data.dimensions).length}>
      <table class="dim-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Size</th>
          </tr>
        </thead>
        <tbody>
          {#each Object.entries(data.dimensions) as [name, size]}
            <tr>
              <td class="mono">{name}</td>
              <td class="mono dim-size">{size}</td>
            </tr>
          {/each}
          {#if Object.keys(data.dimensions).length === 0}
            <tr><td colspan="2" class="text-muted empty-cell">No dimensions</td></tr>
          {/if}
        </tbody>
      </table>
    </CollapsiblePanel>

    <CollapsiblePanel title="Sweeps" badge={data.sweeps.length}>
      <ul class="sweep-list">
        {#each data.sweeps as s}
          <li>
            <button
              class="sweep-item"
              class:active={$selectedSweep === s.index}
              on:click={() => selectSweep(s.index)}
              title="Sweep {s.index}: elevation {s.elevation != null ? s.elevation.toFixed(1) : '?'}deg"
            >
              <span class="sweep-index">{s.index}</span>
              <span class="sweep-angle">{s.elevation != null ? s.elevation.toFixed(1) : '?'}</span>
              <span class="sweep-dims">
                {#if data.dimensions.azimuth}
                  {data.dimensions.azimuth} az
                {/if}
                {#if data.dimensions.range}
                  x {data.dimensions.range} rng
                {/if}
              </span>
            </button>
          </li>
        {/each}
        {#if data.sweeps.length === 0}
          <li class="text-muted empty-row">No sweeps</li>
        {/if}
      </ul>
    </CollapsiblePanel>

    <CollapsiblePanel title="Attributes" badge={Object.keys(data.attributes).length} collapsed={true}>
      <dl class="attr-list">
        {#each Object.entries(data.attributes) as [key, val]}
          <div class="attr-item">
            <dt>{key}</dt>
            <dd title={formatValue(val)}>{truncate(formatValue(val))}</dd>
          </div>
        {/each}
        {#if Object.keys(data.attributes).length === 0}
          <p class="text-muted empty-row">No attributes</p>
        {/if}
      </dl>
    </CollapsiblePanel>
  {:else}
    <div class="empty-state">
      <div class="empty-icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
      </div>
      <p class="empty-title">No file loaded</p>
      <p class="empty-hint">Open a radar file to inspect its contents</p>
      <div class="empty-shortcut">
        <kbd>Ctrl</kbd> + <kbd>O</kbd>
      </div>
    </div>
  {/if}
</div>

<!-- Modals -->
<DataTable bind:visible={showDataTable} />
<MetadataViewer bind:visible={showMetadata} />

<style>
  .data-inspector {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    background: transparent;
  }

  /* Inspector toolbar */
  .inspector-toolbar {
    display: flex;
    gap: var(--spacing-xs, 4px);
    padding: var(--spacing-sm, 8px) var(--spacing-md, 12px);
    border-bottom: 1px solid var(--glass-border, var(--border-color));
    background: rgba(17, 22, 40, 0.3);
    flex-shrink: 0;
  }

  .toolbar-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 500;
    color: var(--text-secondary, #c9d1d9);
    background: rgba(91, 108, 247, 0.06);
    border: 1px solid rgba(91, 108, 247, 0.12);
    border-radius: var(--radius-sm, 4px);
    cursor: pointer;
    transition: all 120ms ease;
    white-space: nowrap;
  }

  .toolbar-btn:hover {
    background: rgba(91, 108, 247, 0.14);
    color: var(--text-primary, #e6edf3);
    border-color: rgba(91, 108, 247, 0.3);
  }

  /* Variable search */
  .var-search-wrapper {
    padding: 0 0 var(--spacing-sm, 8px) 0;
  }

  .var-search-input {
    width: 100%;
    padding: 4px 8px;
    font-size: 11px;
    background: rgba(0, 0, 0, 0.3);
    border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.08));
    border-radius: var(--radius-sm, 4px);
    color: var(--text-primary, #e6edf3);
    outline: none;
    box-sizing: border-box;
  }

  .var-search-input:focus {
    border-color: var(--accent-primary, #5b6cf7);
  }

  .var-search-input::placeholder {
    color: var(--text-muted, #8b949e);
  }

  /* Variable list */
  .var-list,
  .sweep-list {
    list-style: none;
    padding: 0;
    margin: 0;
  }

  .var-item,
  .sweep-item {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    width: 100%;
    padding: 5px var(--spacing-sm);
    background: transparent;
    border: none;
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    font-size: 12px;
    text-align: left;
    cursor: pointer;
    height: auto;
    transition: all 120ms cubic-bezier(0.4, 0, 0.2, 1);
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .var-item:hover,
  .sweep-item:hover {
    background: rgba(91, 108, 247, 0.06);
  }

  .var-item.active,
  .sweep-item.active {
    background: rgba(91, 108, 247, 0.14);
    color: #fff;
    box-shadow: inset 0 0 0 1px rgba(91, 108, 247, 0.15);
  }

  .var-item.active .var-icon,
  .sweep-item.active .sweep-index {
    background: var(--accent-primary);
    color: #fff;
    box-shadow: 0 0 8px rgba(91, 108, 247, 0.3);
  }

  .var-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    font-size: 10px;
    font-weight: 700;
    color: var(--accent-primary);
    background: rgba(91, 108, 247, 0.1);
    border: 1px solid rgba(91, 108, 247, 0.1);
    border-radius: 4px;
    flex-shrink: 0;
    transition: all 120ms ease;
  }

  .var-name {
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sweep-index {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 20px;
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    background: rgba(140, 160, 250, 0.06);
    border: 1px solid rgba(140, 160, 250, 0.08);
    border-radius: 4px;
    flex-shrink: 0;
    transition: all 120ms ease;
  }

  .sweep-angle {
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .sweep-angle::after {
    content: '\00B0';
    margin-left: 1px;
    color: var(--text-muted);
  }

  .sweep-dims {
    font-size: 10px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    margin-left: auto;
    flex-shrink: 0;
    opacity: 0.7;
  }

  /* Dimensions table */
  .dim-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }

  .dim-table th {
    text-align: left;
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    padding: var(--spacing-xs) var(--spacing-sm);
    border-bottom: 1px solid var(--glass-border, var(--border-color));
  }

  .dim-table td {
    padding: var(--spacing-xs) var(--spacing-sm);
    border-bottom: 1px solid var(--glass-border, var(--border-color));
  }

  .dim-table tr:last-child td {
    border-bottom: none;
  }

  .dim-table tr:hover td {
    background: rgba(91, 108, 247, 0.03);
  }

  .dim-size {
    text-align: right;
    color: var(--text-accent);
    font-variant-numeric: tabular-nums;
  }

  /* Attributes */
  .attr-list {
    margin: 0;
    padding: 0;
  }

  .attr-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: var(--spacing-xs) var(--spacing-sm);
    border-bottom: 1px solid var(--glass-border, var(--border-color));
    transition: background 120ms ease;
  }

  .attr-item:last-child {
    border-bottom: none;
  }

  .attr-item:hover {
    background: rgba(91, 108, 247, 0.03);
  }

  .attr-item dt {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-secondary);
    font-family: var(--font-mono);
  }

  .attr-item dd {
    font-size: 11px;
    color: var(--text-muted);
    margin: 0;
    word-break: break-all;
  }

  .empty-row {
    padding: var(--spacing-xs) var(--spacing-sm);
    font-size: 12px;
  }

  .empty-cell {
    font-size: 12px;
    padding: var(--spacing-sm);
  }

  /* Empty state */
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-2xl);
    color: var(--text-muted);
    text-align: center;
    height: 100%;
  }

  .empty-state .empty-icon {
    opacity: 0.2;
    margin-bottom: var(--spacing-sm);
    color: var(--accent-primary);
  }

  .empty-title {
    font-size: 13px;
    color: var(--text-secondary);
    margin: 0;
  }

  .empty-hint {
    font-size: 11px;
    color: var(--text-muted);
    margin: 0;
  }

  .empty-shortcut {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-top: var(--spacing-sm);
    font-size: 11px;
    color: var(--text-muted);
  }

  .empty-shortcut :global(kbd) {
    display: inline-block;
    padding: 2px 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    background: rgba(8, 10, 20, 0.6);
    border: 1px solid var(--glass-border, var(--border-color));
    border-radius: 4px;
    color: var(--text-secondary);
  }
</style>
