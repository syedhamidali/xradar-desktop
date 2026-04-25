/**
 * Web Worker: binary sweep data reassembly.
 *
 * Receives chunked ArrayBuffer data from the WebSocket, combines them into a
 * contiguous buffer, and parses the azimuth / range / values Float32Arrays
 * according to the sweep_data header byte offsets.
 *
 * Returns parsed arrays via transferable (zero-copy back to main thread).
 */

export interface BinaryWorkerInput {
  /** Raw binary chunks from the WebSocket */
  chunks: ArrayBuffer[];
  /** Byte length of the azimuth array segment */
  azimuthBytes: number;
  /** Byte length of the range array segment */
  rangeBytes: number;
  /** Metadata forwarded from the sweep_data JSON header */
  header: {
    variable: string;
    sweep: number;
    n_az: number;
    n_range: number;
    max_range: number;
    vmin: number;
    vmax: number;
    units: string;
  };
}

export interface BinaryWorkerOutput {
  azimuth: Float32Array;
  range: Float32Array;
  values: Float32Array;
  header: BinaryWorkerInput['header'];
  reassemblyTimeMs: number;
}

self.onmessage = (e: MessageEvent<BinaryWorkerInput>) => {
  const t0 = performance.now();
  const { chunks, azimuthBytes, rangeBytes, header } = e.data;

  // Reassemble into a single contiguous buffer
  let totalBytes = 0;
  for (const chunk of chunks) {
    totalBytes += chunk.byteLength;
  }

  const combined = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(new Uint8Array(chunk), offset);
    offset += chunk.byteLength;
  }

  // Parse Float32Arrays from the combined buffer
  const buf = combined.buffer;
  const azimuth = new Float32Array(buf, 0, azimuthBytes / 4);
  const range = new Float32Array(buf, azimuthBytes, rangeBytes / 4);
  const values = new Float32Array(buf, azimuthBytes + rangeBytes);

  // We need to create copies because the subarray views share the underlying
  // buffer and we can only transfer the whole buffer once.
  const azCopy = new Float32Array(azimuth);
  const rngCopy = new Float32Array(range);
  const valCopy = new Float32Array(values);

  const reassemblyTimeMs = performance.now() - t0;

  const output: BinaryWorkerOutput = {
    azimuth: azCopy,
    range: rngCopy,
    values: valCopy,
    header,
    reassemblyTimeMs,
  };

  (self as unknown as Worker).postMessage(output, [
    azCopy.buffer,
    rngCopy.buffer,
    valCopy.buffer,
  ]);
};
