<script lang="ts">
  import { onMount, onDestroy, tick } from 'svelte';
  import { radarData, selectedVariable, selectedSweep, connectionStatus } from '../stores/radarData';
  import { wsManager } from '../utils/websocket';
  import { getCachedSweep, getDefaultCmap, getDefaultRange, type SweepDataEntry } from '../utils/ppiRenderer';
  import { COLORMAP_DATA } from '../utils/colormaps';
  import type { SweepInfo } from '../stores/radarData';

  let container: HTMLDivElement;
  let canvas: HTMLCanvasElement;
  let gl: WebGL2RenderingContext | null = null;
  let program: WebGLProgram | null = null;
  let isLoading = $state(false);
  let hasRendered = $state(false);
  let errorMsg = $state('');

  // Derived from stores
  const variable = $derived($selectedVariable);
  const sweep = $derived($selectedSweep);
  const sweeps = $derived($radarData.sweeps);
  const isConnected = $derived($connectionStatus === 'connected');
  const hasData = $derived($radarData.filePath !== null || $radarData.variables.length > 0);

  // Display settings
  let currentVmin = $state(0);
  let currentVmax = $state(1);
  let currentCmap = $state('turbo');
  let elevationScale = $state(5);
  let opacity = $state(0.85);

  // All loaded sweep data
  let loadedSweeps: Map<number, SweepDataEntry> = $state(new Map());
  let requestedSweeps: Set<string> = $state(new Set());
  let unsubSweepData: (() => void) | null = null;

  // Camera (orbit)
  let camTheta = $state(-30);   // azimuth degrees
  let camPhi = $state(55);      // elevation degrees
  let camDist = $state(2.5);    // distance factor
  let camTarget = $state([0, 0, 0]);
  let isDragging = $state(false);
  let isShiftDrag = $state(false);
  let lastMouse = $state([0, 0]);

  // Mesh buffers per sweep
  let sweepMeshes: { vao: WebGLVertexArrayObject; triCount: number }[] = $state([]);
  let animFrameId = 0;
  let dirty = $state(true);

  // Request missing sweeps whenever connection, variable, or sweep metadata changes.
  let prevVariable = '';
  $effect(() => {
    if (variable && isConnected && sweeps.length > 0) {
      if (variable !== prevVariable) {
        prevVariable = variable;
        currentCmap = getDefaultCmap(variable);
        const range = getDefaultRange(variable);
        if (range) { currentVmin = range[0]; currentVmax = range[1]; }
        loadedSweeps = new Map();
        requestedSweeps = new Set();
        hasRendered = false;
      }

      let requestedAny = false;
      for (const s of sweeps) {
        const key = `${variable}:${s.index}`;
        if (loadedSweeps.has(s.index) || requestedSweeps.has(key)) continue;

        const cached = getCachedSweep(variable, s.index);
        if (cached) {
          loadedSweeps.set(s.index, cached);
        } else {
          requestedSweeps.add(key);
          wsManager.requestSweepData(variable, s.index);
          requestedAny = true;
        }
      }

      if (loadedSweeps.size > 0) {
        buildAndRender();
      }
      isLoading = requestedAny || loadedSweeps.size < sweeps.length;
    }
  });

  onMount(() => {
    unsubSweepData = wsManager.onMessage('sweep_data_ready', (entry: SweepDataEntry) => {
      if (entry.variable === variable) {
        requestedSweeps.delete(`${entry.variable}:${entry.sweep}`);
        loadedSweeps.set(entry.sweep, entry);
        loadedSweeps = loadedSweeps;
        isLoading = loadedSweeps.size < sweeps.length;
        buildAndRender();
      }
    });
  });

  onDestroy(() => {
    unsubSweepData?.();
    if (animFrameId) cancelAnimationFrame(animFrameId);
    cleanupGL();
  });

  // ─── Shaders ──────────────────────────────────────────────────────────

  const VERT = `#version 300 es
    precision highp float;
    in vec3 a_position;  // world-space position (meters)
    in vec4 a_color;     // RGBA [0,255]
    uniform mat4 u_mvp;
    uniform float u_opacity;
    out vec4 v_color;
    void main() {
      v_color = vec4(a_color.rgb / 255.0, a_color.a / 255.0 * u_opacity);
      gl_Position = u_mvp * vec4(a_position, 1.0);
    }
  `;

  const FRAG = `#version 300 es
    precision highp float;
    in vec4 v_color;
    out vec4 fragColor;
    void main() {
      if (v_color.a < 0.01) discard;
      fragColor = v_color;
    }
  `;

  // ─── GL setup ─────────────────────────────────────────────────────────

  function initGL() {
    if (!canvas) return false;
    gl = canvas.getContext('webgl2', { antialias: true, alpha: true });
    if (!gl) { errorMsg = 'WebGL2 not available'; return false; }

    const vs = compileShader(gl, gl.VERTEX_SHADER, VERT);
    const fs = compileShader(gl, gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return false;

    program = gl.createProgram()!;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      errorMsg = 'Shader link failed';
      return false;
    }

    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0.06, 0.06, 0.1, 1.0);
    return true;
  }

  function compileShader(g: WebGL2RenderingContext, type: number, src: string): WebGLShader | null {
    const s = g.createShader(type)!;
    g.shaderSource(s, src);
    g.compileShader(s);
    if (!g.getShaderParameter(s, g.COMPILE_STATUS)) {
      console.error('[3D] Shader compile error:', g.getShaderInfoLog(s));
      return null;
    }
    return s;
  }

  function cleanupGL() {
    if (!gl) return;
    for (const m of sweepMeshes) gl.deleteVertexArray(m.vao);
    sweepMeshes = [];
    if (program) gl.deleteProgram(program);
    gl = null;
    program = null;
  }

  // ─── Build triangle mesh for one sweep ────────────────────────────────

  function buildSweepMesh(
    entry: SweepDataEntry,
    elevRad: number,
    elevScaleVal: number,
    cmapRgb: Uint8Array,
    vmin: number,
    vmax: number,
    maxRange: number,
  ): { positions: Float32Array; colors: Uint8Array; indices: Uint32Array } | null {
    const { azimuth, range, values, nAz, nRange } = entry;
    if (nAz < 2 || nRange < 2) return null;

    const vRange = vmax - vmin;
    const nodata = -9998;
    const cosElev = Math.cos(elevRad);
    const sinElev = Math.sin(elevRad);

    // Pre-compute trig
    const sinAz = new Float32Array(nAz);
    const cosAz = new Float32Array(nAz);
    for (let ai = 0; ai < nAz; ai++) {
      const rad = (azimuth[ai] * Math.PI) / 180;
      sinAz[ai] = Math.sin(rad);
      cosAz[ai] = Math.cos(rad);
    }

    // Build vertex grid: nAz × nRange vertices
    // Normalize positions to [-1, 1] range for stable rendering
    const scale = 1.0 / maxRange;
    const nVerts = nAz * nRange;
    const positions = new Float32Array(nVerts * 3);
    const colors = new Uint8Array(nVerts * 4);

    for (let ai = 0; ai < nAz; ai++) {
      const sa = sinAz[ai];
      const ca = cosAz[ai];
      for (let ri = 0; ri < nRange; ri++) {
        const idx = ai * nRange + ri;
        const r = range[ri];
        const groundR = r * cosElev * scale;
        const z = r * sinElev * elevScaleVal * scale;

        positions[idx * 3]     = groundR * sa;
        positions[idx * 3 + 1] = groundR * ca;
        positions[idx * 3 + 2] = z;

        const v = values[idx];
        if (v <= nodata) {
          colors[idx * 4 + 3] = 0; // transparent for nodata
        } else {
          const t = vRange > 0 ? Math.max(0, Math.min(1, (v - vmin) / vRange)) : 0.5;
          const ci = Math.round(t * 255) * 3;
          colors[idx * 4]     = cmapRgb[ci];
          colors[idx * 4 + 1] = cmapRgb[ci + 1];
          colors[idx * 4 + 2] = cmapRgb[ci + 2];
          colors[idx * 4 + 3] = 220;
        }
      }
    }

    // Build triangle indices: two triangles per grid cell
    // Each cell connects (ai, ri), (ai+1, ri), (ai+1, ri+1), (ai, ri+1)
    // Wrap around azimuth (last az connects back to first)
    const nCells = nAz * (nRange - 1);
    const indices = new Uint32Array(nCells * 6);
    let ii = 0;

    for (let ai = 0; ai < nAz; ai++) {
      const nextAi = (ai + 1) % nAz;
      for (let ri = 0; ri < nRange - 1; ri++) {
        const v00 = ai * nRange + ri;
        const v10 = nextAi * nRange + ri;
        const v01 = ai * nRange + ri + 1;
        const v11 = nextAi * nRange + ri + 1;

        // Triangle 1
        indices[ii++] = v00;
        indices[ii++] = v10;
        indices[ii++] = v01;
        // Triangle 2
        indices[ii++] = v10;
        indices[ii++] = v11;
        indices[ii++] = v01;
      }
    }

    return { positions, colors, indices };
  }

  // ─── Upload mesh to GPU ───────────────────────────────────────────────

  function uploadMesh(
    g: WebGL2RenderingContext,
    prog: WebGLProgram,
    mesh: { positions: Float32Array; colors: Uint8Array; indices: Uint32Array },
  ): { vao: WebGLVertexArrayObject; triCount: number } {
    const vao = g.createVertexArray()!;
    g.bindVertexArray(vao);

    // Positions
    const posBuf = g.createBuffer()!;
    g.bindBuffer(g.ARRAY_BUFFER, posBuf);
    g.bufferData(g.ARRAY_BUFFER, mesh.positions, g.STATIC_DRAW);
    const posLoc = g.getAttribLocation(prog, 'a_position');
    g.enableVertexAttribArray(posLoc);
    g.vertexAttribPointer(posLoc, 3, g.FLOAT, false, 0, 0);

    // Colors
    const colBuf = g.createBuffer()!;
    g.bindBuffer(g.ARRAY_BUFFER, colBuf);
    g.bufferData(g.ARRAY_BUFFER, mesh.colors, g.STATIC_DRAW);
    const colLoc = g.getAttribLocation(prog, 'a_color');
    g.enableVertexAttribArray(colLoc);
    g.vertexAttribPointer(colLoc, 4, g.UNSIGNED_BYTE, false, 0, 0);

    // Indices
    const idxBuf = g.createBuffer()!;
    g.bindBuffer(g.ELEMENT_ARRAY_BUFFER, idxBuf);
    g.bufferData(g.ELEMENT_ARRAY_BUFFER, mesh.indices, g.STATIC_DRAW);

    g.bindVertexArray(null);

    return { vao, triCount: mesh.indices.length };
  }

  // ─── Camera matrix math ───────────────────────────────────────────────

  function perspective(fov: number, aspect: number, near: number, far: number): Float32Array {
    const f = 1.0 / Math.tan(fov / 2);
    const nf = 1 / (near - far);
    return new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) * nf, -1,
      0, 0, 2 * far * near * nf, 0,
    ]);
  }

  function lookAt(eye: number[], center: number[], up: number[]): Float32Array {
    const z = normalize(sub(eye, center));
    const x = normalize(cross(up, z));
    const y = cross(z, x);
    return new Float32Array([
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -dot(x, eye), -dot(y, eye), -dot(z, eye), 1,
    ]);
  }

  function mul4(a: Float32Array, b: Float32Array): Float32Array {
    const r = new Float32Array(16);
    for (let col = 0; col < 4; col++) {
      for (let row = 0; row < 4; row++) {
        r[col * 4 + row] =
          a[row] * b[col * 4] +
          a[4 + row] * b[col * 4 + 1] +
          a[8 + row] * b[col * 4 + 2] +
          a[12 + row] * b[col * 4 + 3];
      }
    }
    return r;
  }

  function sub(a: number[], b: number[]): number[] { return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]; }
  function dot(a: number[], b: number[]): number { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; }
  function cross(a: number[], b: number[]): number[] {
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
  }
  function normalize(v: number[]): number[] {
    const l = Math.sqrt(dot(v, v)) || 1;
    return [v[0]/l, v[1]/l, v[2]/l];
  }

  function getMVP(): Float32Array {
    const w = canvas?.width || 800;
    const h = canvas?.height || 600;
    const proj = perspective(Math.PI / 4, w / h, 0.01, 100);

    const thetaRad = (camTheta * Math.PI) / 180;
    const phiRad = (camPhi * Math.PI) / 180;
    const eye = [
      camDist * Math.cos(phiRad) * Math.sin(thetaRad) + camTarget[0],
      camDist * Math.cos(phiRad) * Math.cos(thetaRad) + camTarget[1],
      camDist * Math.sin(phiRad) + camTarget[2],
    ];
    const view = lookAt(eye, camTarget, [0, 0, 1]);
    return mul4(proj, view);
  }

  // ─── Mouse controls ───────────────────────────────────────────────────

  function onMouseDown(e: MouseEvent) {
    isDragging = true;
    isShiftDrag = e.shiftKey;
    lastMouse = [e.clientX, e.clientY];
  }

  function onMouseMove(e: MouseEvent) {
    if (!isDragging) return;
    const dx = e.clientX - lastMouse[0];
    const dy = e.clientY - lastMouse[1];
    lastMouse = [e.clientX, e.clientY];

    if (isShiftDrag) {
      // Pan
      const scale = camDist * 0.002;
      const thetaRad = (camTheta * Math.PI) / 180;
      camTarget = [
        camTarget[0] - dx * scale * Math.cos(thetaRad),
        camTarget[1] + dx * scale * Math.sin(thetaRad),
        camTarget[2] + dy * scale,
      ];
    } else {
      // Orbit
      camTheta += dx * 0.5;
      camPhi = Math.max(5, Math.min(89, camPhi - dy * 0.5));
    }
    dirty = true;
  }

  function onMouseUp() {
    isDragging = false;
    isShiftDrag = false;
  }

  function onWheel(e: WheelEvent) {
    e.preventDefault();
    camDist *= 1 + e.deltaY * 0.001;
    camDist = Math.max(0.3, Math.min(20, camDist));
    dirty = true;
  }

  // ─── Resize ───────────────────────────────────────────────────────────

  function resizeCanvas() {
    if (!canvas || !container) return;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    gl?.viewport(0, 0, canvas.width, canvas.height);
    dirty = true;
  }

  // ─── Render loop ──────────────────────────────────────────────────────

  function renderFrame() {
    animFrameId = requestAnimationFrame(renderFrame);
    if (!dirty || !gl || !program) return;
    dirty = false;

    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(program);

    const mvp = getMVP();
    gl.uniformMatrix4fv(gl.getUniformLocation(program, 'u_mvp'), false, mvp);
    gl.uniform1f(gl.getUniformLocation(program, 'u_opacity'), opacity);

    for (const mesh of sweepMeshes) {
      gl.bindVertexArray(mesh.vao);
      gl.drawElements(gl.TRIANGLES, mesh.triCount, gl.UNSIGNED_INT, 0);
    }

    gl.bindVertexArray(null);
  }

  // ─── Main build and render ────────────────────────────────────────────

  async function buildAndRender() {
    if (loadedSweeps.size === 0 || !container) return;

    await tick();

    // Init GL if needed
    if (!gl) {
      if (!canvas) return;
      if (!initGL()) return;
      resizeCanvas();
      new ResizeObserver(resizeCanvas).observe(container);
      renderFrame();
    }

    // Clear old meshes
    for (const m of sweepMeshes) gl!.deleteVertexArray(m.vao);
    sweepMeshes = [];

    const cmapRgb = COLORMAP_DATA[currentCmap] ?? COLORMAP_DATA['turbo'];

    // Find max range for normalization
    let maxRange = 1;
    for (const entry of loadedSweeps.values()) {
      if (entry.maxRange > maxRange) maxRange = entry.maxRange;
    }

    for (const [sweepIdx, entry] of loadedSweeps) {
      const sweepInfo = sweeps.find((s) => s.index === sweepIdx);
      if (!sweepInfo) continue;

      const elev = sweepInfo.elevation ?? 0;
      const elevRad = (elev * Math.PI) / 180;

      const mesh = buildSweepMesh(
        entry, elevRad, elevationScale, cmapRgb,
        currentVmin, currentVmax, maxRange,
      );

      if (mesh) {
        sweepMeshes.push(uploadMesh(gl!, program!, mesh));
      }
    }

    hasRendered = true;
    errorMsg = '';
    dirty = true;

    const totalTris = sweepMeshes.reduce((s, m) => s + m.triCount / 3, 0);
    console.log(`[3D] Rendered ${sweepMeshes.length} sweep surfaces, ${Math.round(totalTris).toLocaleString()} triangles`);
  }

  function handleElevScaleChange(e: Event) {
    elevationScale = parseFloat((e.target as HTMLInputElement).value);
    if (loadedSweeps.size > 0) buildAndRender();
  }

  function handleOpacityChange(e: Event) {
    opacity = parseFloat((e.target as HTMLInputElement).value);
    dirty = true;
  }

  function resetCamera() {
    camTheta = -30;
    camPhi = 55;
    camDist = 2.5;
    camTarget = [0, 0, 0];
    dirty = true;
  }
</script>

<div class="viewer-3d">
  {#if hasData}
    <div class="controls-3d">
      <span class="label-3d">3D Volume View</span>
      {#if variable}
        <span class="var-badge">{variable}</span>
      {/if}
      <div class="ctrl-group">
        <label for="elev-scale">Height</label>
        <input id="elev-scale" type="range" min="1" max="20" step="0.5"
               value={elevationScale} on:input={handleElevScaleChange} />
        <span class="ctrl-val">{elevationScale}x</span>
      </div>
      <div class="ctrl-group">
        <label for="opacity-ctrl">Opacity</label>
        <input id="opacity-ctrl" type="range" min="0.1" max="1" step="0.05"
               value={opacity} on:input={handleOpacityChange} />
        <span class="ctrl-val">{Math.round(opacity * 100)}%</span>
      </div>
      <button class="reset-btn" on:click={resetCamera} title="Reset camera">Reset</button>
      {#if loadedSweeps.size > 0}
        <span class="sweep-count">{loadedSweeps.size}/{sweeps.length} sweeps</span>
      {/if}
    </div>

    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="canvas-3d" bind:this={container}
         on:mousedown={onMouseDown}
         on:mousemove={onMouseMove}
         on:mouseup={onMouseUp}
         on:mouseleave={onMouseUp}
         on:wheel|preventDefault={onWheel}>
      <canvas bind:this={canvas}></canvas>
      {#if isLoading}
        <div class="loading-overlay">
          <div class="loading-spinner"></div>
          <p>Loading {loadedSweeps.size}/{sweeps.length} sweeps...</p>
        </div>
      {:else if errorMsg}
        <div class="error-overlay">
          <p>{errorMsg}</p>
        </div>
      {:else if !hasRendered}
        <div class="placeholder-overlay">
          <p>Select a variable to view in 3D</p>
        </div>
      {/if}
    </div>

    <div class="hint-3d">
      Drag to orbit · Scroll to zoom · Shift+drag to pan
    </div>
  {:else}
    <div class="placeholder-overlay full">
      <p>Open a radar file to view in 3D</p>
    </div>
  {/if}
</div>

<style>
  .viewer-3d {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-primary);
    overflow: hidden;
  }

  .controls-3d {
    display: flex;
    align-items: center;
    gap: var(--spacing-md);
    padding: var(--spacing-sm) var(--spacing-md);
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border-color);
    flex-shrink: 0;
    flex-wrap: wrap;
  }

  .label-3d {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    white-space: nowrap;
  }

  .var-badge {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--accent-primary);
    background: rgba(91, 108, 247, 0.1);
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    border: 1px solid rgba(91, 108, 247, 0.2);
  }

  .ctrl-group {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .ctrl-group label {
    font-size: 10px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    font-weight: 600;
    white-space: nowrap;
  }

  .ctrl-group input[type="range"] {
    width: 70px;
    accent-color: var(--accent-primary);
  }

  .ctrl-val {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-secondary);
    min-width: 32px;
  }

  .reset-btn {
    font-size: 10px;
    padding: 2px 8px;
    border: 1px solid var(--border-color);
    border-radius: var(--radius-sm);
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    cursor: pointer;
  }
  .reset-btn:hover {
    background: var(--accent-primary);
    color: white;
    border-color: var(--accent-primary);
  }

  .sweep-count {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-muted);
    margin-left: auto;
  }

  .canvas-3d {
    flex: 1;
    position: relative;
    overflow: hidden;
    cursor: grab;
  }
  .canvas-3d:active { cursor: grabbing; }

  .canvas-3d canvas {
    display: block;
  }

  .hint-3d {
    padding: 3px var(--spacing-md);
    font-size: 10px;
    color: var(--text-muted);
    text-align: center;
    background: var(--bg-tertiary);
    border-top: 1px solid var(--border-color);
  }

  .loading-overlay, .error-overlay, .placeholder-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--spacing-md);
    color: var(--text-muted);
    z-index: 5;
    pointer-events: none;
  }

  .placeholder-overlay.full {
    position: relative;
    height: 100%;
  }

  .error-overlay { color: var(--accent-danger); }

  .loading-spinner {
    width: 32px;
    height: 32px;
    border: 3px solid var(--border-color);
    border-top-color: var(--accent-primary);
    border-radius: 50%;
    animation: spin 800ms linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }
</style>
