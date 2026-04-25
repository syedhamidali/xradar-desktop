import { get } from 'svelte/store';
import {
  connectionStatus,
  radarData,
  selectedVariable,
  selectedSweep,
  renderResult,
  processingProgress,
  exportNotification,
} from '../stores/radarData';
import { addToast } from '../stores/toasts';
import { cacheSweepData, clearSweepCache, getCachedSweep, type SweepDataEntry } from './ppiRenderer';
import { addFile, getActiveFile, type FileEntry } from '../stores/fileManager';
import { perfStats } from '../stores/perfStats';
import type { BinaryWorkerInput, BinaryWorkerOutput } from '../workers/binaryWorker';

type MessageHandler = (data: any) => void;

class WebSocketManager {
  private ws: WebSocket | null = null;
  private port: number | null = null;
  private retryCount = 0;
  private maxRetries = 15;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private messageHandlers: Map<string, MessageHandler[]> = new Map();
  private baseDelay = 500; // ms
  private authToken: string | null = null;
  private authTokenPromise: Promise<string | null> | null = null;

  // Binary reassembly worker
  private binaryWorker: Worker | null = null;
  private binaryWorkerReady = false;
  /** Queue to track isQc flag for in-flight binary worker requests (FIFO). */
  private binaryWorkerQcQueue: boolean[] = [];
  /** Queue to track original message type for in-flight binary worker requests (FIFO). */
  private _binaryWorkerTypeQueue: string[] = [];

  // Prefetch tracking — set of "variable:sweep" keys currently being prefetched
  private prefetchInFlight = new Set<string>();

  /**
   * Connect to the Python sidecar WebSocket server.
   */
  connect(port: number): void {
    // Skip if already connected to this port
    if (this.port === port && this.ws?.readyState === WebSocket.OPEN) {
      return;
    }
    // Skip if already connecting to this port
    if (this.port === port && this.ws?.readyState === WebSocket.CONNECTING) {
      return;
    }
    this.port = port;
    this.retryCount = 0;
    // Cancel any pending reconnect timer
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.initBinaryWorker();
    this.doConnect();
  }

  /** Spawn the binary reassembly Web Worker. */
  private initBinaryWorker(): void {
    if (this.binaryWorker) return; // already initialized
    try {
      this.binaryWorker = new Worker(
        new URL('../workers/binaryWorker.ts', import.meta.url),
        { type: 'module' }
      );
      this.binaryWorkerReady = true;

      this.binaryWorker.onmessage = (e: MessageEvent<BinaryWorkerOutput>) => {
        const { azimuth, range, values, header, reassemblyTimeMs } = e.data;
        perfStats.update(s => ({ ...s, dataTransferTimeMs: reassemblyTimeMs }));

        const entry: SweepDataEntry = {
          variable: header.variable,
          sweep: header.sweep,
          azimuth,
          range,
          values,
          nAz: header.n_az,
          nRange: header.n_range,
          maxRange: header.max_range,
          vmin: header.vmin,
          vmax: header.vmax,
          units: header.units,
        };

        const isQc = this.binaryWorkerQcQueue.shift() ?? false;
        const msgType = this._binaryWorkerTypeQueue.shift() ?? 'sweep_data';
        this.finalizeSweepEntry(entry, isQc, msgType);
      };

      this.binaryWorker.onerror = (err) => {
        console.warn('[WS] Binary worker error, falling back to main thread:', err);
        this.binaryWorkerReady = false;
      };
    } catch (err) {
      console.warn('[WS] Could not create binary worker, using main thread:', err);
      this.binaryWorkerReady = false;
    }
  }

  private doConnect(): void {
    if (!this.port) return;

    // Clean up any existing connection
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onclose = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close();
      }
    }

    connectionStatus.set('connecting');
    const url = `ws://localhost:${this.port}`;

    try {
      this.ws = new WebSocket(url);
      this.ws.binaryType = 'arraybuffer';
    } catch (err) {
      console.error('[WS] Failed to create WebSocket:', err);
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log(`[WS] Connected to ${url}`);
      document.title = `xradar desktop — Connected`;
      connectionStatus.set('connected');
      this.retryCount = 0;

    };

    this.ws.onclose = (event) => {
      console.log(`[WS] Disconnected (code: ${event.code}, reason: ${event.reason})`);
      document.title = `xradar desktop — WS closed ${event.code}`;
      connectionStatus.set('disconnected');
      this.scheduleReconnect();
    };

    this.ws.onerror = (event) => {
      console.error('[WS] Error:', event);
      document.title = `xradar desktop — WS error (retry ${this.retryCount})`;
    };

    this.ws.onmessage = (event) => {
      this.handleMessage(event.data);
    };
  }

  private async getAuthToken(): Promise<string | null> {
    if (this.authToken !== null) return this.authToken;
    if (this.authTokenPromise) return this.authTokenPromise;

    this.authTokenPromise = (async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        const token = await invoke<string>('get_sidecar_token');
        this.authToken = typeof token === 'string' && token.length > 0 ? token : null;
      } catch {
        this.authToken = null;
      } finally {
        this.authTokenPromise = null;
      }
      return this.authToken;
    })();

    return this.authTokenPromise;
  }

  private scheduleReconnect(): void {
    if (this.retryCount >= this.maxRetries) {
      console.warn(`[WS] Max retries (${this.maxRetries}) reached. Giving up.`);
      connectionStatus.set('disconnected');

      return;
    }

    const delay = this.baseDelay * Math.pow(2, this.retryCount);
    this.retryCount++;
    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.retryCount}/${this.maxRetries})...`);

    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
    }

    this.retryTimer = setTimeout(() => {
      this.doConnect();
    }, delay);
  }

  private pendingSweepHeader: any = null;
  private pendingChunks: Uint8Array[] = [];
  private pendingChunksExpected = 0;

  /** Track the path of the file currently being opened (set before send). */
  private _pendingOpenPath: string | null = null;

  private handleMessage(raw: string | ArrayBuffer): void {
    // Binary message → chunk of sweep data following a sweep_data JSON header
    if (typeof raw !== 'string') {
      if (this.pendingSweepHeader) {
        this.pendingChunks.push(new Uint8Array(raw as ArrayBuffer));

        if (this.pendingChunks.length >= this.pendingChunksExpected) {
          const header = this.pendingSweepHeader;
          const chunks = this.pendingChunks;
          this.pendingSweepHeader = null;
          this.pendingChunks = [];
          this.pendingChunksExpected = 0;

          // Reassemble contiguous buffer
          const totalBytes = chunks.reduce((s, c) => s + c.byteLength, 0);
          const combined = new Uint8Array(totalBytes);
          let offset = 0;
          for (const chunk of chunks) {
            combined.set(chunk, offset);
            offset += chunk.byteLength;
          }

          // Volume/cross-section data is a raw float32 grid, not azimuth+range+values
          if (header.type === 'volume_data' || header.type === 'cross_section_3d_data') {
            this.handleRawBinaryData(header, combined.buffer);
          } else {
            this.handleBinarySweepData(header, combined.buffer);
          }
        }
      }
      return;
    }

    let msg: any;
    try {
      msg = JSON.parse(raw);
    } catch (err) {
      console.error('[WS] Failed to parse message:', raw);
      return;
    }

    const type: string = msg.type;
    if (!type) {
      console.warn('[WS] Message has no type field:', msg);
      return;
    }

    // Dispatch to built-in store handlers
    switch (type) {
      case 'file_opened': {
        const vars: string[] = msg.data?.variables ?? [];
        const swps = msg.data?.sweeps ?? [];
        const fileId: string | undefined = msg.file_id;
        const openedPath = this._pendingOpenPath ?? '';
        this._pendingOpenPath = null;

        clearSweepCache();
        radarData.update((state) => ({
          ...state,
          variables: vars,
          dimensions: msg.data?.dimensions ?? {},
          attributes: msg.data?.attributes ?? {},
          sweeps: swps,
          filePath: openedPath || state.filePath,
        }));

        const firstVar = vars.length > 0 ? vars[0] : null;
        const firstSweep = swps.length > 0 ? swps[0].index : 0;

        // Register in the multi-file manager if we got a file_id
        if (fileId && openedPath) {
          const filename = openedPath.split('/').pop() ?? openedPath;
          const entry: FileEntry = {
            id: fileId,
            path: openedPath,
            filename,
            variables: vars,
            sweeps: swps,
            dimensions: msg.data?.dimensions ?? {},
            attributes: msg.data?.attributes ?? {},
            selectedVariable: firstVar,
            selectedSweep: firstSweep,
          };
          addFile(entry);
        }

        if (firstVar) {
          selectedVariable.set(firstVar);
          selectedSweep.set(firstSweep);
          addToast('success', `Loaded ${vars.length} variables, ${swps.length} sweeps`);
          this.requestSweepData(firstVar, firstSweep);
        }
        break;
      }

      case 'sweep_data':
      case 'qc_result':
      case 'difference_result':
      case 'retrieval_data':
      case 'volume_data':
      case 'cross_section_3d_data':
        // JSON header for incoming binary data — store it and wait for chunk frames
        this.pendingSweepHeader = msg;
        this.pendingChunks = [];
        this.pendingChunksExpected = typeof msg.chunks === 'number' ? msg.chunks : 1;
        break;

      case 'render_result':
        renderResult.set({
          image: msg.image ?? null,
          metadata: msg.metadata ?? null,
        });
        break;

      case 'progress':
        processingProgress.set({
          percent: msg.percent ?? 0,
          message: msg.message ?? '',
        });
        // Clear progress when complete
        if (msg.percent >= 100) {
          setTimeout(() => processingProgress.set(null), 2000);
        }
        break;

      case 'export_complete':
        exportNotification.set(msg.path ?? 'Export complete');
        setTimeout(() => exportNotification.set(null), 5000);
        break;

      case 'batch_export_complete':
        exportNotification.set(
          `Batch export complete: ${msg.count ?? 0} files in ${msg.output_dir ?? 'temp'}`
        );
        setTimeout(() => exportNotification.set(null), 5000);
        break;

      case 'animation_export_complete':
        exportNotification.set(msg.path ?? 'Animation export complete');
        setTimeout(() => exportNotification.set(null), 5000);
        break;

      case 'cross_section_result':
      case 'vertical_profile_result':
        // Handled by custom handlers registered in CrossSectionPanel
        break;

      case 'data_table_result':
      case 'metadata_tree_result':
        // Handled by custom handlers registered in DataTable / MetadataViewer
        break;

      case 'error':
        console.error('[WS] Server error:', msg.message);
        // Also clear any processing progress on error
        processingProgress.set(null);
        // Notify user
        this.notifyError(msg.message ?? 'Unknown error from server');
        break;

      default:
        console.log('[WS] Unhandled message type:', type, msg);
    }

    // Dispatch to custom handlers
    const handlers = this.messageHandlers.get(type);
    if (handlers) {
      for (const handler of handlers) {
        try {
          handler(msg);
        } catch (err) {
          console.error(`[WS] Handler error for type '${type}':`, err);
        }
      }
    }
  }

  private notifyError(message: string): void {
    addToast('error', message, 8000);
  }

  /**
   * Register a custom handler for a specific message type.
   */
  onMessage(type: string, handler: MessageHandler): () => void {
    if (!this.messageHandlers.has(type)) {
      this.messageHandlers.set(type, []);
    }
    this.messageHandlers.get(type)!.push(handler);

    // Return an unsubscribe function
    return () => {
      const handlers = this.messageHandlers.get(type);
      if (handlers) {
        const idx = handlers.indexOf(handler);
        if (idx >= 0) handlers.splice(idx, 1);
      }
    };
  }

  /**
   * Handle binary sweep data received after a sweep_data JSON header.
   * Delegates to the binary worker when available, otherwise parses on main thread.
   */
  private handleBinarySweepData(header: any, buffer: ArrayBuffer): void {
    const azBytes = header.azimuth_bytes as number;
    const rngBytes = header.range_bytes as number;
    const msgType: string = header.type ?? 'sweep_data';
    const isQc = msgType === 'qc_result';

    // Try binary worker path
    if (this.binaryWorkerReady && this.binaryWorker) {
      // We need to send the buffer as a transferable chunk
      const input: BinaryWorkerInput = {
        chunks: [buffer],
        azimuthBytes: azBytes,
        rangeBytes: rngBytes,
        header: {
          variable: header.variable,
          sweep: header.sweep,
          n_az: header.n_az,
          n_range: header.n_range,
          max_range: header.max_range,
          vmin: header.vmin,
          vmax: header.vmax,
          units: header.units,
        },
      };
      // Track QC flag and message type in FIFO queues (worker messages arrive in order)
      this.binaryWorkerQcQueue.push(isQc);
      this._binaryWorkerTypeQueue.push(msgType);

      this.binaryWorker.postMessage(input, [buffer]);
      return;
    }

    // Fallback: parse on main thread
    const azimuth = new Float32Array(buffer, 0, azBytes / 4);
    const range = new Float32Array(buffer, azBytes, rngBytes / 4);
    const values = new Float32Array(buffer, azBytes + rngBytes);

    const entry: SweepDataEntry = {
      variable: header.variable,
      sweep: header.sweep,
      azimuth,
      range,
      values,
      nAz: header.n_az,
      nRange: header.n_range,
      maxRange: header.max_range,
      vmin: header.vmin,
      vmax: header.vmax,
      units: header.units,
    };

    this.finalizeSweepEntry(entry, isQc, msgType);
  }

  /**
   * Handle raw binary grid data (volume_data, cross_section_3d_data).
   * These are flat Float32Arrays without azimuth/range preamble.
   */
  private handleRawBinaryData(header: any, buffer: ArrayBuffer): void {
    const data = new Float32Array(buffer);
    const eventType = header.type === 'volume_data' ? 'volume_data' : 'cross_section_3d_data';
    const payload = { ...header, data };
    this._dispatchHandlers(eventType, payload);
  }

  /**
   * Common path after binary data has been parsed (from worker or main thread).
   * Caches the entry, notifies listeners, and triggers prefetch.
   */
  private finalizeSweepEntry(entry: SweepDataEntry, isQc: boolean, msgType = 'sweep_data'): void {
    // Clear prefetch in-flight flag
    this.prefetchInFlight.delete(`${entry.variable}:${entry.sweep}`);

    cacheSweepData(entry);

    // Dispatch to type-specific handlers
    if (isQc) {
      this._dispatchHandlers('qc_data_ready', entry);
    }
    if (msgType === 'difference_result') {
      this._dispatchHandlers('difference_data_ready', entry);
    }
    if (msgType === 'retrieval_data') {
      this._dispatchHandlers('retrieval_data_ready', entry);
    }

    // Always fire sweep_data_ready so the viewer refreshes
    this._dispatchHandlers('sweep_data_ready', entry);

    // Trigger background prefetch of adjacent sweeps (not for special results)
    if (!isQc && msgType === 'sweep_data') {
      this.prefetchAdjacentSweeps(entry.variable, entry.sweep);
    }
  }

  /** Fire all registered handlers for a given event type. */
  private _dispatchHandlers(eventType: string, data: any): void {
    const handlers = this.messageHandlers.get(eventType);
    if (handlers) {
      for (const handler of handlers) {
        try { handler(data); } catch (err) {
          console.error(`[WS] ${eventType} handler error:`, err);
        }
      }
    }
  }

  // ── Prefetch ─────────────────────────────────────────────────────────────

  /**
   * Prefetch sweep N-1 and N+1 in background so switching is instant.
   * Skips if the data is already cached or a request is in flight.
   */
  private prefetchAdjacentSweeps(variable: string, currentSweep: number): void {
    const totalSweeps = get(radarData).sweeps.length;
    const adjacent = [currentSweep - 1, currentSweep + 1];
    for (const s of adjacent) {
      if (s < 0 || s >= totalSweeps) continue;
      const key = `${variable}:${s}`;

      // Already cached or in-flight — skip
      if (getCachedSweep(variable, s)) continue;
      if (this.prefetchInFlight.has(key)) continue;

      this.prefetchInFlight.add(key);
      console.log(`[WS] Prefetching ${key}`);
      this.requestSweepData(variable, s);
    }
  }

  /**
   * Request raw sweep data for client-side rendering.
   * Optionally target a specific open file by its server-assigned id.
   */
  requestSweepData(variable: string, sweep: number, fileId?: string): void {
    const msg: Record<string, any> = { type: 'get_sweep_data', variable, sweep };
    if (fileId) msg.file_id = fileId;
    this.send(msg);
  }

  /**
   * Open a file on the server (multi-file aware).
   * Stores the pending path so the file_opened handler can build a FileEntry.
   */
  openFile(path: string): void {
    this._pendingOpenPath = path;
    this.send({ type: 'open_file', path });
  }

  /**
   * Close a file on the server and release its resources.
   */
  closeFile(fileId: string): void {
    this.send({ type: 'close_file', file_id: fileId });
  }

  /**
   * Send a JSON message to the server.
   */
  async send(message: Record<string, any>): Promise<void> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('[WS] Cannot send, not connected. Message:', message);
      return;
    }

    const payloadMessage = { ...message };
    const token = await this.getAuthToken();
    if (token) {
      payloadMessage.token = token;
    }

    const activeFile = getActiveFile();
    const needsActiveFile =
      activeFile &&
      !payloadMessage.file_id &&
      !payloadMessage.file_id_1 &&
      !payloadMessage.file_ids &&
      payloadMessage.type !== 'open_file' &&
      payloadMessage.type !== 'close_file' &&
      payloadMessage.type !== 'ping' &&
      payloadMessage.type !== 'get_time_series' &&
      payloadMessage.type !== 'get_accumulation';
    if (needsActiveFile) {
      payloadMessage.file_id = activeFile.id;
    }

    const payload = JSON.stringify(payloadMessage);
    this.ws.send(payload);
  }

  /**
   * Disconnect from the server and stop reconnection attempts.
   */
  disconnect(): void {
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.retryCount = this.maxRetries; // Prevent reconnects

    if (this.ws) {
      this.ws.onclose = null; // Prevent reconnect on intentional close
      this.ws.close();
      this.ws = null;
    }

    // Terminate binary worker
    if (this.binaryWorker) {
      this.binaryWorker.terminate();
      this.binaryWorker = null;
      this.binaryWorkerReady = false;
    }

    connectionStatus.set('disconnected');
  }

  /**
   * Whether the WebSocket is currently connected.
   */
  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

// Export a singleton instance
export const wsManager = new WebSocketManager();

// Auto-connect: discover sidecar port and connect.
//
// Primary strategy: call the Rust `get_sidecar_port` command via Tauri invoke.
// This directly asks the Rust backend for the port — no file polling, no event
// timing, no race conditions. Falls back to file polling for non-Tauri contexts.
(function autoConnect() {
  let attempts = 0;

  async function tryTauriInvoke(): Promise<number | null> {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const port = await invoke<number>('get_sidecar_port');
      return port > 0 ? port : null;
    } catch {
      return null;
    }
  }

  async function tryPortFile(): Promise<number | null> {
    try {
      const r = await fetch('/.sidecar-port');
      if (!r.ok) return null;
      const text = await r.text();
      const port = parseInt(text.trim(), 10);
      return port > 0 && port < 65536 ? port : null;
    } catch {
      return null;
    }
  }

  async function tryOnce() {
    if (wsManager.isConnected) return; // done
    attempts++;

    // Strategy 1: Tauri invoke (most reliable inside Tauri)
    let port = await tryTauriInvoke();

    // Strategy 2: check global set by bootstrap script
    if (!port) {
      const globalPort = (window as any).__XRADAR_PORT__;
      if (typeof globalPort === 'number' && globalPort > 0) {
        port = globalPort;
      }
    }

    // Strategy 3: port file fallback (for non-Tauri or edge cases)
    if (!port) {
      port = await tryPortFile();
    }

    if (port && !wsManager.isConnected) {
      console.log(`[WS] autoConnect: discovered port ${port} (attempt ${attempts})`);
      (window as any).__XRADAR_PORT__ = port;
      wsManager.connect(port);
    }

    // Keep polling until connected (up to 120 attempts = ~60 seconds)
    if (!wsManager.isConnected && attempts < 120) {
      setTimeout(tryOnce, 500);
    }
  }

  // Also listen for the Tauri event as a belt-and-suspenders approach
  (async () => {
    try {
      const { listen } = await import('@tauri-apps/api/event');
      const unlisten = await listen<number>('sidecar-ready', (event) => {
        const port = event.payload;
        if (port > 0 && !wsManager.isConnected) {
          console.log(`[WS] autoConnect via Tauri event: port ${port}`);
          (window as any).__XRADAR_PORT__ = port;
          wsManager.connect(port);
        }
        unlisten();
      });
    } catch {
      // Not running inside Tauri — ignore
    }
  })();

  // Start polling after a short delay to let the page settle
  setTimeout(tryOnce, 300);
})();
