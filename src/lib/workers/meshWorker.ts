/**
 * Web Worker: polar-to-Cartesian triangle mesh computation.
 *
 * Moves the heaviest CPU work (the nested loop in uploadSweepData) off the
 * main thread.  Receives Float32Arrays via transferable objects for zero-copy
 * transfer and returns the computed positions + vals the same way.
 */

export interface MeshWorkerInput {
  azimuth: Float32Array;
  range: Float32Array;
  values: Float32Array;
  nAz: number;
  nRange: number;
}

export interface MeshWorkerOutput {
  positions: Float32Array;
  vals: Float32Array;
  vertexCount: number;
  buildTimeMs: number;
}

/**
 * Build the triangle mesh from polar radar data.
 * Identical math to PPIRenderer.uploadSweepData, but runs in a worker.
 */
function buildMesh(input: MeshWorkerInput): MeshWorkerOutput {
  const t0 = performance.now();
  const { azimuth, range, values, nAz, nRange } = input;

  // Worst case: each (az, range) cell produces 2 triangles = 6 vertices.
  // Pre-allocate at max size then trim — avoids dynamic push overhead.
  const maxVerts = nAz * (nRange - 1) * 6;
  const positions = new Float32Array(maxVerts * 2);
  const vals = new Float32Array(maxVerts);

  let vi = 0; // vertex index

  for (let ai = 0; ai < nAz; ai++) {
    const az0Rad = azimuth[ai] * (Math.PI / 180);
    const az1Deg = ai + 1 < nAz ? azimuth[ai + 1] : azimuth[0] + 360;
    const az1Rad = az1Deg * (Math.PI / 180);

    const sinAz0 = Math.sin(az0Rad);
    const cosAz0 = Math.cos(az0Rad);
    const sinAz1 = Math.sin(az1Rad);
    const cosAz1 = Math.cos(az1Rad);

    for (let ri = 0; ri < nRange - 1; ri++) {
      const v = values[ai * nRange + ri];
      if (v <= -9998) continue;

      const r0 = range[ri];
      const r1 = range[ri + 1];

      const x00 = r0 * sinAz0, y00 = r0 * cosAz0;
      const x01 = r1 * sinAz0, y01 = r1 * cosAz0;
      const x10 = r0 * sinAz1, y10 = r0 * cosAz1;
      const x11 = r1 * sinAz1, y11 = r1 * cosAz1;

      // Triangle 1
      const pi = vi * 2;
      positions[pi]     = x00; positions[pi + 1] = y00;
      positions[pi + 2] = x01; positions[pi + 3] = y01;
      positions[pi + 4] = x10; positions[pi + 5] = y10;
      vals[vi]     = v;
      vals[vi + 1] = v;
      vals[vi + 2] = v;
      vi += 3;

      // Triangle 2
      const pi2 = vi * 2;
      positions[pi2]     = x01; positions[pi2 + 1] = y01;
      positions[pi2 + 2] = x11; positions[pi2 + 3] = y11;
      positions[pi2 + 4] = x10; positions[pi2 + 5] = y10;
      vals[vi]     = v;
      vals[vi + 1] = v;
      vals[vi + 2] = v;
      vi += 3;
    }
  }

  // Return trimmed views over the same buffer
  const buildTimeMs = performance.now() - t0;
  return {
    positions: positions.subarray(0, vi * 2),
    vals: vals.subarray(0, vi),
    vertexCount: vi,
    buildTimeMs,
  };
}

// ── Worker message handler ──────────────────────────────────────────────────

self.onmessage = (e: MessageEvent<MeshWorkerInput>) => {
  const result = buildMesh(e.data);

  // Copy into standalone buffers for transfer (subarray shares the original)
  const posBuf = new Float32Array(result.positions);
  const valBuf = new Float32Array(result.vals);

  const output: MeshWorkerOutput = {
    positions: posBuf,
    vals: valBuf,
    vertexCount: result.vertexCount,
    buildTimeMs: result.buildTimeMs,
  };

  (self as unknown as Worker).postMessage(output, [
    posBuf.buffer,
    valBuf.buffer,
  ]);
};
