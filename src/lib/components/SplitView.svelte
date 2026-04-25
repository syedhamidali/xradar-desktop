<script lang="ts">
  import RadarViewer from './RadarViewer.svelte';
  import RadarViewer3D from './RadarViewer3D.svelte';

  /** Layout: '1x1' = single, '1x2' = side-by-side, '2x2' = quad */
  export let layout: '1x1' | '1x2' | '2x2' = '1x1';
  export let viewMode: '2d' | '3d' = '2d';

  $: panelCount = layout === '1x1' ? 1 : layout === '1x2' ? 2 : 4;
  $: gridCols = layout === '1x1' ? '1fr' : '1fr 1fr';
  $: gridRows = layout === '2x2' ? '1fr 1fr' : '1fr';
</script>

<div
  class="split-view"
  style="grid-template-columns: {gridCols}; grid-template-rows: {gridRows};"
>
  {#each Array(panelCount) as _, i (i)}
    <div class="split-panel" class:bordered={panelCount > 1}>
      {#if viewMode === '2d'}
        <RadarViewer />
      {:else}
        <RadarViewer3D />
      {/if}
    </div>
  {/each}
</div>

<style>
  .split-view {
    display: grid;
    width: 100%;
    height: 100%;
    overflow: hidden;
    gap: 0;
  }

  .split-panel {
    overflow: hidden;
    min-width: 0;
    min-height: 0;
  }

  .split-panel.bordered {
    border: 1px solid var(--border-color);
    /* Collapse shared borders */
    margin-right: -1px;
    margin-bottom: -1px;
  }

  .split-panel.bordered:last-child {
    margin-right: 0;
    margin-bottom: 0;
  }
</style>
