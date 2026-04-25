/**
 * Temporal analysis state store.
 *
 * Manages probe point, time series data, diff mode, and timeline state
 * shared between TimeSeriesChart, DiffViewer, and Timeline components.
 */

import { writable, derived, get } from 'svelte/store';
import { openFiles, activeFileId, type FileEntry } from './fileManager';

// ── Types ──────────────────────────────────────────────────────────────────

export interface ProbePoint {
  azimuth: number;   // degrees
  rangeM: number;    // metres
  x: number;         // screen x for tooltip
  y: number;         // screen y for tooltip
}

export interface TimeSeriesPoint {
  fileId: string;
  value: number | null;
  timestamp: string | null;
  label: string; // filename or timestamp
}

export interface TimeSeriesData {
  variable: string;
  sweep: number;
  azimuth: number;
  rangeM: number;
  points: TimeSeriesPoint[];
}

export type DiffMode = 'off' | 'diff';

// ── Stores ─────────────────────────────────────────────────────────────────

/** The currently selected probe point on the PPI. null when no point is set. */
export const probePoint = writable<ProbePoint | null>(null);

/** Active time series data keyed by variable name. */
export const timeSeriesData = writable<Map<string, TimeSeriesData>>(new Map());

/** Which variables are plotted on the time series chart. */
export const timeSeriesVariables = writable<string[]>([]);

/** Current diff mode: 'off' or 'diff'. */
export const diffMode = writable<DiffMode>('off');

/** File ID selected as the "base" for diff comparison. */
export const diffBaseFileId = writable<string | null>(null);

/** File ID selected as the "compare" for diff comparison. */
export const diffCompareFileId = writable<string | null>(null);

/** Timeline playback state. */
export const timelinePlaybackActive = writable<boolean>(false);

/** Index of the currently focused file in the timeline. */
export const timelineIndex = writable<number>(0);

/** Auto-advance interval in ms. */
export const timelineIntervalMs = writable<number>(1000);

// ── Derived ────────────────────────────────────────────────────────────────

/** Open files sorted by filename (proxy for time order). */
export const sortedFiles = derived(openFiles, ($files) => {
  return [...$files].sort((a, b) => a.filename.localeCompare(b.filename));
});

/** File IDs of all open files (for passing to WebSocket). */
export const allFileIds = derived(openFiles, ($files) => $files.map((f) => f.id));

// ── Actions ────────────────────────────────────────────────────────────────

export function setProbePoint(point: ProbePoint | null): void {
  probePoint.set(point);
}

export function clearTimeSeries(): void {
  timeSeriesData.set(new Map());
}

export function updateTimeSeries(variable: string, data: TimeSeriesData): void {
  timeSeriesData.update((map) => {
    const next = new Map(map);
    next.set(variable, data);
    return next;
  });
}

export function toggleDiffMode(): void {
  diffMode.update((m) => (m === 'off' ? 'diff' : 'off'));
}

export function setDiffFiles(baseId: string | null, compareId: string | null): void {
  diffBaseFileId.set(baseId);
  diffCompareFileId.set(compareId);
}
