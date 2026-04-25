/**
 * LRU render cache for pre-built triangle mesh data.
 *
 * Caches positions + values Float32Arrays keyed by "variable:sweep" so that
 * switching back to a previously viewed sweep skips mesh computation entirely.
 *
 * Evicts least-recently-used entries when the cache exceeds a configurable
 * memory budget (default 50 MB).
 */

export interface CachedMesh {
  positions: Float32Array;
  vals: Float32Array;
  vertexCount: number;
  /** variable:sweep key */
  key: string;
}

interface CacheEntry {
  mesh: CachedMesh;
  /** Approximate byte size of the stored arrays */
  byteSize: number;
}

export class RenderCache {
  /** Ordered map — iteration order = insertion/access order (LRU at front) */
  private entries = new Map<string, CacheEntry>();
  private currentBytes = 0;
  private maxBytes: number;

  // Stats
  private _hits = 0;
  private _misses = 0;

  constructor(maxMB = 50) {
    this.maxBytes = maxMB * 1024 * 1024;
  }

  static key(variable: string, sweep: number): string {
    return `${variable}:${sweep}`;
  }

  get(variable: string, sweep: number): CachedMesh | null {
    const k = RenderCache.key(variable, sweep);
    const entry = this.entries.get(k);
    if (!entry) {
      this._misses++;
      return null;
    }
    // Move to end (most-recently-used)
    this.entries.delete(k);
    this.entries.set(k, entry);
    this._hits++;
    return entry.mesh;
  }

  put(variable: string, sweep: number, mesh: CachedMesh): void {
    const k = RenderCache.key(variable, sweep);

    // Remove existing entry if present
    const existing = this.entries.get(k);
    if (existing) {
      this.currentBytes -= existing.byteSize;
      this.entries.delete(k);
    }

    const byteSize = mesh.positions.byteLength + mesh.vals.byteLength;

    // Evict LRU entries until we have space
    while (this.currentBytes + byteSize > this.maxBytes && this.entries.size > 0) {
      const firstKey = this.entries.keys().next().value;
      if (firstKey === undefined) break;
      const evicted = this.entries.get(firstKey)!;
      this.currentBytes -= evicted.byteSize;
      this.entries.delete(firstKey);
    }

    this.entries.set(k, { mesh, byteSize });
    this.currentBytes += byteSize;
  }

  has(variable: string, sweep: number): boolean {
    return this.entries.has(RenderCache.key(variable, sweep));
  }

  clear(): void {
    this.entries.clear();
    this.currentBytes = 0;
    this._hits = 0;
    this._misses = 0;
  }

  // ── Stats for PerformanceMonitor ────────────────────────────────────────

  get hits(): number { return this._hits; }
  get misses(): number { return this._misses; }
  get hitRatio(): number {
    const total = this._hits + this._misses;
    return total === 0 ? 0 : this._hits / total;
  }
  get sizeBytes(): number { return this.currentBytes; }
  get sizeMB(): number { return this.currentBytes / (1024 * 1024); }
  get entryCount(): number { return this.entries.size; }
  get maxSizeMB(): number { return this.maxBytes / (1024 * 1024); }
}

/** Singleton render cache instance */
export const renderCache = new RenderCache(50);
