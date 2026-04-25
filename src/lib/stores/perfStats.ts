/**
 * Writable store for performance timing data.
 *
 * Updated by the renderer / workers and read by PerformanceMonitor.
 */
import { writable } from 'svelte/store';

export interface PerfStatsData {
  /** Time spent in the WebGL render() call (ms) */
  renderTimeMs: number;
  /** Time spent building the triangle mesh in the worker (ms) */
  meshBuildTimeMs: number;
  /** Time transferring data between main thread and workers (ms) */
  dataTransferTimeMs: number;
}

export const perfStats = writable<PerfStatsData>({
  renderTimeMs: 0,
  meshBuildTimeMs: 0,
  dataTransferTimeMs: 0,
});
