<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { wsManager } from '../utils/websocket';

  let { visible = $bindable(false) }: { visible?: boolean } = $props();

  interface MetaNode {
    name: string;
    type: 'group' | 'variable' | 'coordinate' | 'attribute';
    children?: MetaNode[];
    dtype?: string;
    shape?: number[];
    units?: string;
    long_name?: string;
    min?: number | null;
    max?: number | null;
    mean?: number | null;
    value?: any;
    dims?: string[];
    attributes?: Record<string, any>;
  }

  let tree = $state<MetaNode[]>([]);
  let loading = $state(false);
  let errorMsg = $state('');
  let searchQuery = $state('');
  let expandedNodes = $state<Set<string>>(new Set());
  let copiedPath = $state('');
  let copyFeedbackTimeout: ReturnType<typeof setTimeout> | null = null;

  const filteredTree = $derived(searchQuery.trim() ? filterTree(tree, searchQuery.toLowerCase()) : tree);

  function filterTree(nodes: MetaNode[], query: string): MetaNode[] {
    const result: MetaNode[] = [];
    for (const node of nodes) {
      const nameMatch = node.name.toLowerCase().includes(query);
      const longNameMatch = (node.long_name || '').toLowerCase().includes(query);
      const unitsMatch = (node.units || '').toLowerCase().includes(query);
      const valueMatch = node.value !== undefined && String(node.value).toLowerCase().includes(query);
      const filteredChildren = node.children ? filterTree(node.children, query) : [];
      if (nameMatch || longNameMatch || unitsMatch || valueMatch || filteredChildren.length > 0) {
        result.push({
          ...node,
          children: filteredChildren.length > 0 ? filteredChildren : node.children,
        });
        // Auto-expand matched parents
        if (filteredChildren.length > 0) {
          expandedNodes.add(node.name);
          expandedNodes = expandedNodes;
        }
      }
    }
    return result;
  }

  function requestMetadata() {
    loading = true;
    errorMsg = '';
    wsManager.send({ type: 'get_metadata_tree' });
  }

  let unsubMeta: (() => void) | null = null;

  onMount(() => {
    unsubMeta = wsManager.onMessage('metadata_tree_result', (msg: any) => {
      loading = false;
      if (msg.error) {
        errorMsg = msg.error;
        return;
      }
      tree = msg.tree || [];
      // Auto-expand root
      for (const node of tree) {
        expandedNodes.add(node.name);
      }
      expandedNodes = expandedNodes;
    });
  });

  onDestroy(() => {
    if (unsubMeta) unsubMeta();
    if (copyFeedbackTimeout) clearTimeout(copyFeedbackTimeout);
  });

  $effect(() => {
    if (visible) {
      requestMetadata();
    }
  });

  function toggleNode(name: string) {
    if (expandedNodes.has(name)) {
      expandedNodes.delete(name);
    } else {
      expandedNodes.add(name);
    }
    expandedNodes = expandedNodes;
  }

  function copyValue(value: any, path: string) {
    const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
    navigator.clipboard.writeText(text).then(() => {
      copiedPath = path;
      if (copyFeedbackTimeout) clearTimeout(copyFeedbackTimeout);
      copyFeedbackTimeout = setTimeout(() => { copiedPath = ''; }, 1500);
    });
  }

  function formatValue(value: any): string {
    if (value === null || value === undefined) return 'null';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function truncate(s: string, max: number = 80): string {
    return s.length > max ? s.substring(0, max) + '...' : s;
  }

  function nodeIcon(type: string): string {
    switch (type) {
      case 'group': return 'G';
      case 'variable': return 'V';
      case 'coordinate': return 'C';
      case 'attribute': return 'A';
      default: return '?';
    }
  }

  function nodeIconClass(type: string): string {
    switch (type) {
      case 'group': return 'icon-group';
      case 'variable': return 'icon-var';
      case 'coordinate': return 'icon-coord';
      case 'attribute': return 'icon-attr';
      default: return '';
    }
  }

  function close() {
    visible = false;
  }
</script>

{#if visible}
<div class="meta-overlay" on:click|self={close}>
  <div class="meta-panel">
    <div class="meta-header">
      <div class="meta-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />
        </svg>
        <span>Metadata Tree</span>
      </div>
      <div class="meta-actions">
        <button class="meta-btn" on:click={close} title="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
    </div>

    <div class="meta-search-bar">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
      <input
        type="text"
        placeholder="Search metadata..."
        bind:value={searchQuery}
        class="meta-search-input"
      />
      {#if searchQuery}
        <button class="meta-clear-btn" on:click={() => { searchQuery = ''; }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      {/if}
    </div>

    <div class="meta-content">
      {#if loading}
        <div class="meta-loading">
          <div class="meta-spinner"></div>
          <span>Loading metadata...</span>
        </div>
      {:else if errorMsg}
        <div class="meta-error">{errorMsg}</div>
      {:else if filteredTree.length === 0}
        <div class="meta-empty">
          {searchQuery ? 'No results found' : 'No metadata available. Open a file first.'}
        </div>
      {:else}
        <div class="meta-tree">
          {#each filteredTree as node}
            <svelte:self visible={true} />
            {@const isExpanded = expandedNodes.has(node.name)}
            <div class="tree-node">
              <button
                class="tree-row"
                class:expandable={node.children && node.children.length > 0}
                on:click={() => {
                  if (node.children && node.children.length > 0) {
                    toggleNode(node.name);
                  }
                  if (node.value !== undefined) copyValue(node.value, node.name);
                }}
                title={node.value !== undefined ? `Click to copy: ${truncate(formatValue(node.value), 120)}` : node.name}
              >
                {#if node.children && node.children.length > 0}
                  <span class="tree-chevron" class:open={isExpanded}>
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                      <polyline points="9 6 15 12 9 18" />
                    </svg>
                  </span>
                {:else}
                  <span class="tree-spacer"></span>
                {/if}

                <span class="tree-icon {nodeIconClass(node.type)}">{nodeIcon(node.type)}</span>
                <span class="tree-name">{node.name}</span>

                {#if node.dtype}
                  <span class="tree-badge dtype">{node.dtype}</span>
                {/if}
                {#if node.shape}
                  <span class="tree-badge shape">[{node.shape.join(' x ')}]</span>
                {/if}
                {#if node.units}
                  <span class="tree-badge units">{node.units}</span>
                {/if}
                {#if node.min !== null && node.min !== undefined}
                  <span class="tree-stats">
                    {node.min.toFixed(2)} .. {node.max?.toFixed(2)}
                    {#if node.mean !== null && node.mean !== undefined}
                      (avg {node.mean.toFixed(2)})
                    {/if}
                  </span>
                {/if}
                {#if node.value !== undefined && node.type === 'attribute'}
                  <span class="tree-value" class:copied={copiedPath === node.name}>
                    {copiedPath === node.name ? 'Copied!' : truncate(formatValue(node.value), 50)}
                  </span>
                {/if}

                {#if node.long_name}
                  <span class="tree-longname">{truncate(node.long_name, 40)}</span>
                {/if}
              </button>

              {#if isExpanded && node.children}
                <div class="tree-children">
                  {#each node.children as child}
                    {@const childExpanded = expandedNodes.has(child.name)}
                    <div class="tree-node">
                      <button
                        class="tree-row"
                        class:expandable={child.children && child.children.length > 0}
                        on:click={() => {
                          if (child.children && child.children.length > 0) {
                            toggleNode(child.name);
                          }
                          if (child.value !== undefined) copyValue(child.value, child.name);
                        }}
                        title={child.value !== undefined ? `Click to copy: ${truncate(formatValue(child.value), 120)}` : child.name}
                      >
                        {#if child.children && child.children.length > 0}
                          <span class="tree-chevron" class:open={childExpanded}>
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                              <polyline points="9 6 15 12 9 18" />
                            </svg>
                          </span>
                        {:else}
                          <span class="tree-spacer"></span>
                        {/if}

                        <span class="tree-icon {nodeIconClass(child.type)}">{nodeIcon(child.type)}</span>
                        <span class="tree-name">{child.name}</span>

                        {#if child.dtype}
                          <span class="tree-badge dtype">{child.dtype}</span>
                        {/if}
                        {#if child.shape}
                          <span class="tree-badge shape">[{child.shape.join(' x ')}]</span>
                        {/if}
                        {#if child.units}
                          <span class="tree-badge units">{child.units}</span>
                        {/if}
                        {#if child.min !== null && child.min !== undefined}
                          <span class="tree-stats">
                            {child.min.toFixed(2)} .. {child.max?.toFixed(2)}
                            {#if child.mean !== null && child.mean !== undefined}
                              (avg {child.mean.toFixed(2)})
                            {/if}
                          </span>
                        {/if}
                        {#if child.value !== undefined && child.type === 'attribute'}
                          <span class="tree-value" class:copied={copiedPath === child.name}>
                            {copiedPath === child.name ? 'Copied!' : truncate(formatValue(child.value), 50)}
                          </span>
                        {/if}
                        {#if child.long_name}
                          <span class="tree-longname">{truncate(child.long_name, 40)}</span>
                        {/if}
                      </button>

                      {#if childExpanded && child.children}
                        <div class="tree-children">
                          {#each child.children as leaf}
                            <div class="tree-node">
                              <button
                                class="tree-row leaf"
                                on:click={() => {
                                  if (leaf.value !== undefined) copyValue(leaf.value, leaf.name);
                                }}
                                title={leaf.value !== undefined ? `Click to copy: ${truncate(formatValue(leaf.value), 120)}` : leaf.name}
                              >
                                <span class="tree-spacer"></span>
                                <span class="tree-icon {nodeIconClass(leaf.type)}">{nodeIcon(leaf.type)}</span>
                                <span class="tree-name">{leaf.name}</span>
                                {#if leaf.dtype}
                                  <span class="tree-badge dtype">{leaf.dtype}</span>
                                {/if}
                                {#if leaf.shape}
                                  <span class="tree-badge shape">[{leaf.shape.join(' x ')}]</span>
                                {/if}
                                {#if leaf.units}
                                  <span class="tree-badge units">{leaf.units}</span>
                                {/if}
                                {#if leaf.min !== null && leaf.min !== undefined}
                                  <span class="tree-stats">
                                    {leaf.min.toFixed(2)} .. {leaf.max?.toFixed(2)}
                                  </span>
                                {/if}
                                {#if leaf.value !== undefined}
                                  <span class="tree-value" class:copied={copiedPath === leaf.name}>
                                    {copiedPath === leaf.name ? 'Copied!' : truncate(formatValue(leaf.value), 50)}
                                  </span>
                                {/if}
                              </button>
                            </div>
                          {/each}
                        </div>
                      {/if}
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
</div>
{/if}

<style>
  .meta-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .meta-panel {
    width: 70vw;
    height: 75vh;
    max-width: 900px;
    background: var(--bg-primary, #0d1117);
    border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.08));
    border-radius: var(--radius-lg, 12px);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.5);
  }

  .meta-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--spacing-sm, 8px) var(--spacing-md, 12px);
    background: rgba(17, 22, 40, 0.8);
    border-bottom: 1px solid var(--glass-border, rgba(255, 255, 255, 0.08));
    flex-shrink: 0;
  }

  .meta-title {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm, 8px);
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary, #e6edf3);
  }

  .meta-actions {
    display: flex;
    gap: var(--spacing-xs, 4px);
  }

  .meta-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: transparent;
    border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.08));
    border-radius: var(--radius-sm, 4px);
    color: var(--text-muted, #8b949e);
    cursor: pointer;
    transition: all 120ms ease;
  }

  .meta-btn:hover {
    background: rgba(91, 108, 247, 0.1);
    color: var(--text-primary, #e6edf3);
  }

  .meta-search-bar {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm, 8px);
    padding: var(--spacing-xs, 4px) var(--spacing-md, 12px);
    background: rgba(17, 22, 40, 0.5);
    border-bottom: 1px solid var(--glass-border, rgba(255, 255, 255, 0.08));
    color: var(--text-muted, #8b949e);
    flex-shrink: 0;
  }

  .meta-search-input {
    flex: 1;
    padding: 4px 8px;
    font-size: 12px;
    background: rgba(0, 0, 0, 0.3);
    border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.08));
    border-radius: var(--radius-sm, 4px);
    color: var(--text-primary, #e6edf3);
    outline: none;
  }

  .meta-search-input:focus {
    border-color: var(--accent-primary, #5b6cf7);
  }

  .meta-clear-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    background: transparent;
    border: none;
    color: var(--text-muted, #8b949e);
    cursor: pointer;
    border-radius: 50%;
  }

  .meta-clear-btn:hover {
    background: rgba(255, 255, 255, 0.1);
  }

  .meta-content {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .meta-loading, .meta-error, .meta-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--spacing-sm, 8px);
    padding: var(--spacing-2xl, 32px);
    color: var(--text-muted, #8b949e);
    font-size: 13px;
  }

  .meta-error {
    color: var(--error, #f85149);
  }

  .meta-spinner {
    width: 20px;
    height: 20px;
    border: 2px solid rgba(91, 108, 247, 0.2);
    border-top-color: var(--accent-primary, #5b6cf7);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  /* Tree */
  .meta-tree {
    padding: var(--spacing-sm, 8px) 0;
  }

  .tree-node {
    display: flex;
    flex-direction: column;
  }

  .tree-row {
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
    padding: 4px var(--spacing-sm, 8px);
    background: transparent;
    border: none;
    color: var(--text-primary, #e6edf3);
    font-size: 12px;
    text-align: left;
    cursor: default;
    transition: background 80ms ease;
    white-space: nowrap;
    overflow: hidden;
  }

  .tree-row.expandable {
    cursor: pointer;
  }

  .tree-row:hover {
    background: rgba(91, 108, 247, 0.04);
  }

  .tree-chevron {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    flex-shrink: 0;
    transition: transform 200ms ease;
    color: var(--text-muted, #8b949e);
  }

  .tree-chevron.open {
    transform: rotate(90deg);
  }

  .tree-spacer {
    width: 14px;
    flex-shrink: 0;
  }

  .tree-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    font-size: 9px;
    font-weight: 700;
    border-radius: 3px;
    flex-shrink: 0;
  }

  .icon-group {
    background: rgba(91, 108, 247, 0.15);
    color: #7b8cff;
    border: 1px solid rgba(91, 108, 247, 0.15);
  }

  .icon-var {
    background: rgba(56, 189, 248, 0.15);
    color: #38bdf8;
    border: 1px solid rgba(56, 189, 248, 0.15);
  }

  .icon-coord {
    background: rgba(52, 211, 153, 0.15);
    color: #34d399;
    border: 1px solid rgba(52, 211, 153, 0.15);
  }

  .icon-attr {
    background: rgba(251, 191, 36, 0.15);
    color: #fbbf24;
    border: 1px solid rgba(251, 191, 36, 0.15);
  }

  .tree-name {
    font-family: var(--font-mono, monospace);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }

  .tree-badge {
    font-size: 9px;
    padding: 1px 5px;
    border-radius: 3px;
    flex-shrink: 0;
    font-family: var(--font-mono, monospace);
  }

  .tree-badge.dtype {
    background: rgba(139, 92, 246, 0.12);
    color: #a78bfa;
    border: 1px solid rgba(139, 92, 246, 0.12);
  }

  .tree-badge.shape {
    background: rgba(34, 211, 238, 0.1);
    color: #22d3ee;
    border: 1px solid rgba(34, 211, 238, 0.1);
  }

  .tree-badge.units {
    background: rgba(251, 146, 60, 0.12);
    color: #fb923c;
    border: 1px solid rgba(251, 146, 60, 0.12);
  }

  .tree-stats {
    font-size: 10px;
    color: var(--text-muted, #8b949e);
    font-family: var(--font-mono, monospace);
    flex-shrink: 0;
  }

  .tree-value {
    font-size: 10px;
    color: var(--text-muted, #8b949e);
    cursor: pointer;
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    flex-shrink: 1;
    transition: color 150ms ease;
  }

  .tree-value:hover {
    color: var(--accent-primary, #5b6cf7);
  }

  .tree-value.copied {
    color: #34d399;
  }

  .tree-longname {
    font-size: 10px;
    color: var(--text-muted, #8b949e);
    font-style: italic;
    opacity: 0.7;
    flex-shrink: 1;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .tree-children {
    padding-left: 20px;
  }
</style>
