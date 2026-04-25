/**
 * Custom colormap store — persists user-created colormaps in localStorage.
 */
import { writable, get } from 'svelte/store';
import { registerColormap, unregisterColormap, COLORMAP_DATA } from '../utils/colormaps';

// ─── Types ─────────────────────────────────────────────────────────────

export interface ColorStop {
  position: number; // 0–1
  color: [number, number, number]; // [r, g, b] 0–255
}

export interface CustomColormap {
  name: string;
  stops: ColorStop[];
  isReversed: boolean;
  isDiscrete: boolean;
  discreteSteps: number; // only used when isDiscrete=true
}

// ─── Interpolation ─────────────────────────────────────────────────────

/** Interpolate color stops into a 256x3 Uint8Array. */
export function interpolateColormap(
  stops: ColorStop[],
  numSteps: number = 256,
  isReversed: boolean = false,
  isDiscrete: boolean = false,
  discreteSteps: number = 16,
): Uint8Array {
  const sorted = [...stops].sort((a, b) => a.position - b.position);
  if (sorted.length === 0) return new Uint8Array(numSteps * 3);
  if (sorted.length === 1) {
    const arr = new Uint8Array(numSteps * 3);
    for (let i = 0; i < numSteps; i++) {
      arr[i * 3] = sorted[0].color[0];
      arr[i * 3 + 1] = sorted[0].color[1];
      arr[i * 3 + 2] = sorted[0].color[2];
    }
    return arr;
  }

  const arr = new Uint8Array(numSteps * 3);
  for (let i = 0; i < numSteps; i++) {
    let t = i / (numSteps - 1);
    if (isReversed) t = 1 - t;
    if (isDiscrete && discreteSteps > 1) {
      t = Math.floor(t * discreteSteps) / (discreteSteps - 1);
      t = Math.min(1, t);
    }

    // Find surrounding stops
    let lo = sorted[0];
    let hi = sorted[sorted.length - 1];
    for (let j = 0; j < sorted.length - 1; j++) {
      if (t >= sorted[j].position && t <= sorted[j + 1].position) {
        lo = sorted[j];
        hi = sorted[j + 1];
        break;
      }
    }

    const range = hi.position - lo.position;
    const segT = range > 0 ? (t - lo.position) / range : 0;
    const clamped = Math.max(0, Math.min(1, segT));

    arr[i * 3] = Math.round(lo.color[0] + (hi.color[0] - lo.color[0]) * clamped);
    arr[i * 3 + 1] = Math.round(lo.color[1] + (hi.color[1] - lo.color[1]) * clamped);
    arr[i * 3 + 2] = Math.round(lo.color[2] + (hi.color[2] - lo.color[2]) * clamped);
  }
  return arr;
}

// ─── Persistence ───────────────────────────────────────────────────────

const STORAGE_KEY = 'xradar-custom-colormaps';

function loadFromStorage(): CustomColormap[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return [];
}

function saveToStorage(cmaps: CustomColormap[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cmaps));
  } catch { /* ignore */ }
}

// ─── Store ─────────────────────────────────────────────────────────────

const initial = loadFromStorage();
export const customColormaps = writable<CustomColormap[]>(initial);

// Register all loaded custom colormaps into COLORMAP_DATA on init
initial.forEach((cm) => {
  const rgb = interpolateColormap(cm.stops, 256, cm.isReversed, cm.isDiscrete, cm.discreteSteps);
  registerColormap(cm.name, rgb);
});

/** Add or update a custom colormap. */
export function saveCustomColormap(cm: CustomColormap): void {
  const rgb = interpolateColormap(cm.stops, 256, cm.isReversed, cm.isDiscrete, cm.discreteSteps);
  registerColormap(cm.name, rgb);

  customColormaps.update((list) => {
    const idx = list.findIndex((c) => c.name === cm.name);
    const next = [...list];
    if (idx >= 0) {
      next[idx] = cm;
    } else {
      next.push(cm);
    }
    saveToStorage(next);
    return next;
  });
}

/** Delete a custom colormap by name. */
export function deleteCustomColormap(name: string): void {
  unregisterColormap(name);
  customColormaps.update((list) => {
    const next = list.filter((c) => c.name !== name);
    saveToStorage(next);
    return next;
  });
}

/** Get a custom colormap by name. */
export function getCustomColormap(name: string): CustomColormap | undefined {
  return get(customColormaps).find((c) => c.name === name);
}

/** Export a custom colormap as a JSON string. */
export function exportColormapJSON(cm: CustomColormap): string {
  return JSON.stringify(cm, null, 2);
}

/** Import a colormap from a JSON string, returns null on error. */
export function importColormapJSON(json: string): CustomColormap | null {
  try {
    const parsed = JSON.parse(json);
    if (
      typeof parsed.name === 'string' &&
      Array.isArray(parsed.stops) &&
      parsed.stops.length >= 2
    ) {
      const cm: CustomColormap = {
        name: parsed.name,
        stops: parsed.stops.map((s: any) => ({
          position: Math.max(0, Math.min(1, Number(s.position))),
          color: [
            Math.max(0, Math.min(255, Math.round(Number(s.color[0])))),
            Math.max(0, Math.min(255, Math.round(Number(s.color[1])))),
            Math.max(0, Math.min(255, Math.round(Number(s.color[2])))),
          ] as [number, number, number],
        })),
        isReversed: !!parsed.isReversed,
        isDiscrete: !!parsed.isDiscrete,
        discreteSteps: parsed.discreteSteps ?? 16,
      };
      return cm;
    }
  } catch { /* ignore */ }
  return null;
}
