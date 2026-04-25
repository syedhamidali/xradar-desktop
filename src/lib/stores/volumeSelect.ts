import { writable } from 'svelte/store';

/** Whether the box-select tool is active on the PPI */
export const boxSelectActive = writable(false);

/** The last selected box (meters from radar center), or null */
export const selectedBox = writable<{
  xMin: number; xMax: number; yMin: number; yMax: number;
} | null>(null);
