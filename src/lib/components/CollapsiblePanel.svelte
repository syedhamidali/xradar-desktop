<script lang="ts">
  export let title: string;
  export let badge: string | number | null = null;
  export let collapsed: boolean = false;
  export let icon: string = '';

  function toggle() {
    collapsed = !collapsed;
  }
</script>

<div class="collapsible-panel" class:collapsed>
  <button class="panel-header" on:click={toggle} title="{collapsed ? 'Expand' : 'Collapse'} {title}">
    <div class="panel-header-left">
      {#if icon}
        <span class="panel-icon">{icon}</span>
      {/if}
      <span class="panel-title">{title}</span>
    </div>
    <div class="panel-header-right">
      {#if badge !== null}
        <span class="panel-badge">{badge}</span>
      {/if}
      <span class="panel-chevron" class:open={!collapsed}>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </span>
    </div>
  </button>
  <div class="panel-content" class:visible={!collapsed}>
    <div class="panel-body">
      <slot />
    </div>
  </div>
</div>

<style>
  .collapsible-panel {
    border-bottom: 1px solid var(--glass-border, var(--border-color));
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: var(--spacing-sm) var(--spacing-md);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-secondary);
    background: rgba(17, 22, 40, 0.5);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    border: none;
    border-bottom: 1px solid var(--glass-border, var(--border-color));
    border-radius: 0;
    cursor: pointer;
    transition: all var(--transition-fast);
    height: 36px;
  }

  .panel-header:hover {
    background: rgba(91, 108, 247, 0.06);
    color: var(--text-primary);
  }

  .panel-header-left {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
  }

  .panel-header-right {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
  }

  .panel-icon {
    font-size: 12px;
    opacity: 0.7;
  }

  .panel-title {
    white-space: nowrap;
  }

  .panel-badge {
    display: inline-flex;
    align-items: center;
    font-size: 10px;
    font-weight: 600;
    color: var(--accent-hover, var(--text-accent));
    background: rgba(91, 108, 247, 0.1);
    padding: 2px 7px;
    border-radius: 10px;
    min-width: 20px;
    justify-content: center;
    border: 1px solid rgba(91, 108, 247, 0.1);
  }

  .panel-chevron {
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 250ms cubic-bezier(0.4, 0, 0.2, 1);
    color: var(--text-muted);
  }

  .panel-chevron.open {
    transform: rotate(0deg);
  }

  .panel-chevron:not(.open) {
    transform: rotate(-90deg);
  }

  .panel-content {
    max-height: 0;
    overflow: hidden;
    transition: max-height 250ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .panel-content.visible {
    max-height: 2000px;
    transition: max-height 350ms cubic-bezier(0.4, 0, 0.2, 1);
  }

  .panel-body {
    padding: var(--spacing-md);
  }
</style>
