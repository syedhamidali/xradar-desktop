<script lang="ts">
  import { createEventDispatcher, onMount, onDestroy, tick } from 'svelte';
  import { radarData, selectedVariable, selectedSweep } from '../stores/radarData';
  import { colormap } from '../stores/settings';

  export let visible = false;

  const dispatch = createEventDispatcher();

  let query = '';
  let selectedIndex = 0;
  let inputEl: HTMLInputElement;

  interface PaletteAction {
    id: string;
    label: string;
    category: string;
    shortcut?: string;
    icon: string;
    action: () => void;
  }

  const isMac = typeof navigator !== 'undefined' && /Mac/.test(navigator.platform);
  const mod = isMac ? '\u2318' : 'Ctrl';

  function getActions(): PaletteAction[] {
    const actions: PaletteAction[] = [
      {
        id: 'open-file',
        label: 'Open File',
        category: 'File',
        shortcut: `${mod}+O`,
        icon: '\u{1F4C2}',
        action: () => dispatch('action', { type: 'open-file' }),
      },
      {
        id: 'toggle-left-sidebar',
        label: 'Toggle Data Inspector',
        category: 'View',
        shortcut: `${mod}+B`,
        icon: '\u{1F4CB}',
        action: () => dispatch('action', { type: 'toggle-left-sidebar' }),
      },
      {
        id: 'toggle-right-sidebar',
        label: 'Toggle Tools Panel',
        category: 'View',
        shortcut: `${mod}+E`,
        icon: '\u{1F527}',
        action: () => dispatch('action', { type: 'toggle-right-sidebar' }),
      },
      {
        id: 'toggle-animation',
        label: 'Play / Pause Animation',
        category: 'Playback',
        shortcut: 'Space',
        icon: '\u25B6',
        action: () => dispatch('action', { type: 'toggle-animation' }),
      },
      {
        id: 'view-2d',
        label: 'Switch to 2D View',
        category: 'View',
        icon: '\u{1F5FA}',
        action: () => dispatch('action', { type: 'set-view', value: '2d' }),
      },
      {
        id: 'view-3d',
        label: 'Switch to 3D View',
        category: 'View',
        icon: '\u{1F30D}',
        action: () => dispatch('action', { type: 'set-view', value: '3d' }),
      },
    ];

    // Add variable selection actions
    const data = $radarData;
    for (const v of data.variables) {
      actions.push({
        id: `var-${v}`,
        label: `Select Variable: ${v}`,
        category: 'Variables',
        icon: 'V',
        action: () => {
          selectedVariable.set(v);
        },
      });
    }

    // Add sweep selection actions
    for (const s of data.sweeps) {
      const elev = s.elevation != null ? s.elevation.toFixed(1) : '?';
      actions.push({
        id: `sweep-${s.index}`,
        label: `Select Sweep ${s.index} (${elev}\u00B0)`,
        category: 'Sweeps',
        icon: '\u{1F4E1}',
        action: () => {
          selectedSweep.set(s.index);
        },
      });
    }

    // Add colormap options
    const cmaps = ['viridis', 'plasma', 'inferno', 'magma', 'cividis', 'turbo', 'jet', 'rainbow', 'coolwarm', 'RdBu_r', 'pyart_NWSRef', 'pyart_NWSVel'];
    for (const cm of cmaps) {
      actions.push({
        id: `cmap-${cm}`,
        label: `Colormap: ${cm}`,
        category: 'Colormap',
        icon: '\u{1F3A8}',
        action: () => {
          colormap.set(cm);
        },
      });
    }

    // Export action
    actions.push({
      id: 'export',
      label: 'Export Current View',
      category: 'File',
      icon: '\u{1F4BE}',
      action: () => dispatch('action', { type: 'export' }),
    });

    return actions;
  }

  function fuzzyMatch(text: string, pattern: string): boolean {
    const lowerText = text.toLowerCase();
    const lowerPattern = pattern.toLowerCase();
    if (lowerPattern === '') return true;

    // Split pattern into words and check if all words appear in text
    const words = lowerPattern.split(/\s+/);
    return words.every(word => lowerText.includes(word));
  }

  $: allActions = getActions();
  $: filtered = query === ''
    ? allActions.slice(0, 12)
    : allActions.filter(a => fuzzyMatch(`${a.label} ${a.category}`, query)).slice(0, 12);
  $: selectedIndex = Math.min(selectedIndex, Math.max(0, filtered.length - 1));

  $: if (visible) {
    focusInput();
  }

  async function focusInput() {
    await tick();
    if (inputEl) inputEl.focus();
  }

  function close() {
    visible = false;
    query = '';
    selectedIndex = 0;
    dispatch('close');
  }

  function executeAction(action: PaletteAction) {
    action.action();
    close();
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = (selectedIndex + 1) % filtered.length;
      scrollToSelected();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = (selectedIndex - 1 + filtered.length) % filtered.length;
      scrollToSelected();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (filtered[selectedIndex]) {
        executeAction(filtered[selectedIndex]);
      }
    }
  }

  function scrollToSelected() {
    tick().then(() => {
      const el = document.querySelector('.cmd-item.selected');
      if (el) el.scrollIntoView({ block: 'nearest' });
    });
  }

  function handleBackdropClick(e: MouseEvent) {
    if ((e.target as HTMLElement).classList.contains('cmd-backdrop')) {
      close();
    }
  }

  // Reset selection on query change
  $: if (query !== undefined) selectedIndex = 0;
</script>

{#if visible}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="cmd-backdrop" on:click={handleBackdropClick}>
    <div class="cmd-palette" on:keydown={handleKeyDown}>
      <div class="cmd-input-wrap">
        <svg class="cmd-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          bind:this={inputEl}
          bind:value={query}
          class="cmd-input"
          placeholder="Type a command..."
          spellcheck="false"
          autocomplete="off"
        />
        <kbd class="cmd-esc-hint">ESC</kbd>
      </div>

      <div class="cmd-results">
        {#if filtered.length === 0}
          <div class="cmd-empty">No matching commands</div>
        {:else}
          {#each filtered as action, i}
            <!-- svelte-ignore a11y-click-events-have-key-events -->
            <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
            <div
              class="cmd-item"
              class:selected={i === selectedIndex}
              role="option"
              aria-selected={i === selectedIndex}
              on:click={() => executeAction(action)}
              on:mouseenter={() => (selectedIndex = i)}
            >
              <span class="cmd-item-icon">{action.icon}</span>
              <div class="cmd-item-body">
                <span class="cmd-item-label">{action.label}</span>
                <span class="cmd-item-category">{action.category}</span>
              </div>
              {#if action.shortcut}
                <kbd class="cmd-item-shortcut">{action.shortcut}</kbd>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

      <div class="cmd-footer">
        <span class="cmd-footer-hint">
          <kbd>\u2191</kbd><kbd>\u2193</kbd> navigate
        </span>
        <span class="cmd-footer-hint">
          <kbd>\u21B5</kbd> select
        </span>
        <span class="cmd-footer-hint">
          <kbd>esc</kbd> close
        </span>
      </div>
    </div>
  </div>
{/if}

<style>
  .cmd-backdrop {
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 15vh;
    animation: fade-in 120ms ease-out;
  }

  @keyframes fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  @keyframes slide-in {
    from { opacity: 0; transform: translateY(-12px) scale(0.97); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }

  .cmd-palette {
    width: 520px;
    max-width: 90vw;
    background: rgba(13, 16, 32, 0.92);
    backdrop-filter: blur(40px);
    -webkit-backdrop-filter: blur(40px);
    border: 1px solid rgba(140, 160, 250, 0.12);
    border-radius: 14px;
    box-shadow:
      0 0 0 1px rgba(140, 160, 250, 0.06),
      0 24px 80px rgba(0, 0, 0, 0.7),
      0 0 60px rgba(91, 108, 247, 0.06);
    overflow: hidden;
    animation: slide-in 180ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .cmd-input-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 16px;
    border-bottom: 1px solid rgba(140, 160, 250, 0.08);
  }

  .cmd-search-icon {
    color: var(--text-muted);
    flex-shrink: 0;
    opacity: 0.6;
  }

  .cmd-input {
    flex: 1;
    background: transparent;
    border: none;
    outline: none;
    font-size: 15px;
    font-family: var(--font-sans);
    color: var(--text-primary);
    caret-color: var(--accent-primary);
  }

  .cmd-input::placeholder {
    color: var(--text-muted);
    opacity: 0.6;
  }

  .cmd-esc-hint {
    font-size: 9px;
    opacity: 0.5;
  }

  .cmd-results {
    max-height: 360px;
    overflow-y: auto;
    padding: 6px;
  }

  .cmd-empty {
    padding: 24px;
    text-align: center;
    color: var(--text-muted);
    font-size: 13px;
  }

  .cmd-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 80ms ease;
  }

  .cmd-item:hover,
  .cmd-item.selected {
    background: rgba(91, 108, 247, 0.1);
  }

  .cmd-item.selected {
    background: rgba(91, 108, 247, 0.14);
    box-shadow: inset 0 0 0 1px rgba(91, 108, 247, 0.12);
  }

  .cmd-item-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    font-size: 13px;
    background: rgba(91, 108, 247, 0.08);
    border-radius: 6px;
    flex-shrink: 0;
    color: var(--text-secondary);
  }

  .cmd-item-body {
    flex: 1;
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }

  .cmd-item-label {
    font-size: 13px;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .cmd-item-category {
    font-size: 11px;
    color: var(--text-muted);
    white-space: nowrap;
    opacity: 0.7;
  }

  .cmd-item-shortcut {
    font-size: 10px;
    opacity: 0.7;
    flex-shrink: 0;
  }

  .cmd-footer {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 16px;
    border-top: 1px solid rgba(140, 160, 250, 0.06);
  }

  .cmd-footer-hint {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: var(--text-muted);
    opacity: 0.6;
  }

  .cmd-footer-hint :global(kbd) {
    font-size: 9px;
    padding: 1px 4px;
  }
</style>
