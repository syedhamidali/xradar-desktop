import { writable, get } from 'svelte/store';

export interface Toast {
  id: number;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
}

let nextId = 1;

export const toasts = writable<Toast[]>([]);

export function addToast(level: Toast['level'], message: string, durationMs = 5000): void {
  const id = nextId++;
  toasts.update((t) => [...t, { id, level, message }]);
  if (durationMs > 0) {
    setTimeout(() => dismissToast(id), durationMs);
  }
}

export function dismissToast(id: number): void {
  toasts.update((t) => t.filter((toast) => toast.id !== id));
}
