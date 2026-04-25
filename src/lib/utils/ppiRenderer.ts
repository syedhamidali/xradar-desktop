/**
 * WebGL-based PPI (Plan Position Indicator) radar renderer.
 *
 * Uses real matplotlib colormaps (24 palettes including NWS standards),
 * triangle-mesh polar-to-Cartesian rendering, and 1D colormap GPU textures.
 *
 * Enhanced features:
 *  - Bilinear interpolation (smooth mode) between adjacent gates
 *  - Edge detection / contour lines at configurable dBZ intervals
 *  - Transparent no-data regions (map backgrounds show through)
 *  - Radial vignette effect
 *  - Cross-fade sweep transitions via requestAnimationFrame
 *  - Optional FPS counter
 *  - Sin/cos lookup tables for fast polar-to-Cartesian conversion
 */

import {
  COLORMAP_DATA,
  COLORMAP_NAMES,
  VARIABLE_CMAP_DEFAULTS,
  VARIABLE_RANGE_DEFAULTS,
} from './colormaps';
import { renderCache, type CachedMesh } from './renderCache';
import { perfStats } from '../stores/perfStats';
import type { MeshWorkerInput, MeshWorkerOutput } from '../workers/meshWorker';

// ─── Sweep data cache ────────────────────────────────────────────────────
export interface SweepDataEntry {
  variable: string;
  sweep: number;
  azimuth: Float32Array;
  range: Float32Array;
  values: Float32Array;
  nAz: number;
  nRange: number;
  maxRange: number;
  vmin: number;
  vmax: number;
  units: string;
}

const sweepCache = new Map<string, SweepDataEntry>();

function cacheKey(variable: string, sweep: number): string {
  return `${variable}:${sweep}`;
}

export function cacheSweepData(entry: SweepDataEntry): void {
  const key = cacheKey(entry.variable, entry.sweep);
  sweepCache.set(key, entry);
  if (sweepCache.size > 200) {
    const first = sweepCache.keys().next().value;
    if (first !== undefined) sweepCache.delete(first);
  }
}

export function getCachedSweep(variable: string, sweep: number): SweepDataEntry | undefined {
  return sweepCache.get(cacheKey(variable, sweep));
}

export function clearSweepCache(): void {
  sweepCache.clear();
  renderCache.clear();
}

// ─── Colormap helpers ───────────────────────────────────────────────────

/** Get the default colormap name for a radar variable. */
export function getDefaultCmap(variable: string): string {
  if (VARIABLE_CMAP_DEFAULTS[variable]) return VARIABLE_CMAP_DEFAULTS[variable];
  const vl = variable.toLowerCase();
  if (vl.includes('dbz') || vl.includes('reflectivity') || vl.includes('power')) return 'NWSRef';
  if (vl.includes('vel') || vl.includes('vrad')) return 'NWSVel';
  if (vl.includes('width') || vl.includes('wrad')) return 'hot';
  if (vl.includes('zdr') || vl.includes('differential_reflectivity')) return 'Spectral_r';
  if (vl.includes('rhohv') || vl.includes('correlation')) return 'viridis';
  if (vl.includes('phidp') || vl.includes('phase')) return 'plasma';
  if (vl.includes('kdp')) return 'YlGnBu';
  return 'turbo';
}

/** Get default vmin/vmax for a variable. */
export function getDefaultRange(variable: string): [number, number] | null {
  return VARIABLE_RANGE_DEFAULTS[variable] ?? null;
}

/** Build RGBA Uint8Array (256x4) from a named colormap for GPU upload. */
export function buildColormapRGBA(cmapName: string): Uint8Array {
  const rgb = COLORMAP_DATA[cmapName] ?? COLORMAP_DATA['turbo'];
  const rgba = new Uint8Array(256 * 4);
  for (let i = 0; i < 256; i++) {
    rgba[i * 4] = rgb[i * 3];
    rgba[i * 4 + 1] = rgb[i * 3 + 1];
    rgba[i * 4 + 2] = rgb[i * 3 + 2];
    rgba[i * 4 + 3] = 255;
  }
  return rgba;
}

/** Get RGB [r,g,b] for index i from a named colormap. */
export function getColormapRGB(cmapName: string, index: number): [number, number, number] {
  const rgb = COLORMAP_DATA[cmapName] ?? COLORMAP_DATA['turbo'];
  const i = Math.max(0, Math.min(255, Math.round(index)));
  return [rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]];
}

// ─── Sin/Cos lookup table ──────────────────────────────────────────────

const TRIG_TABLE_SIZE = 3600; // 0.1-degree resolution
const sinLUT = new Float32Array(TRIG_TABLE_SIZE);
const cosLUT = new Float32Array(TRIG_TABLE_SIZE);

for (let i = 0; i < TRIG_TABLE_SIZE; i++) {
  const rad = (i / TRIG_TABLE_SIZE) * Math.PI * 2;
  sinLUT[i] = Math.sin(rad);
  cosLUT[i] = Math.cos(rad);
}

function fastSin(degrees: number): number {
  const idx = (((degrees % 360) + 360) % 360) * (TRIG_TABLE_SIZE / 360);
  const i0 = Math.floor(idx) % TRIG_TABLE_SIZE;
  const i1 = (i0 + 1) % TRIG_TABLE_SIZE;
  const frac = idx - Math.floor(idx);
  return sinLUT[i0] * (1 - frac) + sinLUT[i1] * frac;
}

function fastCos(degrees: number): number {
  const idx = (((degrees % 360) + 360) % 360) * (TRIG_TABLE_SIZE / 360);
  const i0 = Math.floor(idx) % TRIG_TABLE_SIZE;
  const i1 = (i0 + 1) % TRIG_TABLE_SIZE;
  const frac = idx - Math.floor(idx);
  return cosLUT[i0] * (1 - frac) + cosLUT[i1] * frac;
}

// ─── Shaders ────────────────────────────────────────────────────────────

// --- Sharp (original flat-color) vertex + fragment ---
// Extended with vignette, transparent bg, and transition alpha.

const VERTEX_SHADER_SHARP = `
  attribute vec2 a_position;
  attribute float a_value;
  uniform mat3 u_matrix;
  varying float v_value;
  varying vec2 v_pos;
  void main() {
    vec3 pos = u_matrix * vec3(a_position, 1.0);
    gl_Position = vec4(pos.xy, 0.0, 1.0);
    v_value = a_value;
    v_pos = a_position;
  }
`;

const FRAGMENT_SHADER_SHARP = `
  precision mediump float;
  varying float v_value;
  varying vec2 v_pos;
  uniform sampler2D u_colormap;
  uniform float u_vmin;
  uniform float u_vmax;
  uniform float u_nodata;
  uniform vec3 u_bg;
  uniform float u_bgAlpha;
  uniform float u_transparentBg;
  uniform float u_vignetteIntensity;
  uniform float u_maxRange;
  uniform float u_transitionAlpha;
  void main() {
    if (v_value <= u_nodata + 0.5) {
      if (u_transparentBg > 0.5) {
        gl_FragColor = vec4(0.0, 0.0, 0.0, 0.0);
      } else {
        gl_FragColor = vec4(u_bg, u_bgAlpha);
      }
      return;
    }
    float t = clamp((v_value - u_vmin) / (u_vmax - u_vmin), 0.0, 1.0);
    vec4 color = texture2D(u_colormap, vec2(t, 0.5));
    // Vignette
    if (u_vignetteIntensity > 0.0 && u_maxRange > 0.0) {
      float dist = length(v_pos) / u_maxRange;
      float vig = 1.0 - u_vignetteIntensity * smoothstep(0.4, 1.0, dist);
      color.rgb *= vig;
    }
    color.a *= u_transitionAlpha;
    gl_FragColor = color;
  }
`;

// --- Smooth (bilinear interpolation) vertex + fragment ---

const VERTEX_SHADER_SMOOTH = `
  attribute vec2 a_position;
  attribute vec2 a_polar;
  attribute vec4 a_values4;
  uniform mat3 u_matrix;
  varying vec2 v_polar;
  varying vec4 v_values4;
  varying vec2 v_pos;
  void main() {
    vec3 pos = u_matrix * vec3(a_position, 1.0);
    gl_Position = vec4(pos.xy, 0.0, 1.0);
    v_polar = a_polar;
    v_values4 = a_values4;
    v_pos = a_position;
  }
`;

const FRAGMENT_SHADER_SMOOTH = `
  precision mediump float;
  varying vec2 v_polar;
  varying vec4 v_values4;
  varying vec2 v_pos;
  uniform sampler2D u_colormap;
  uniform float u_vmin;
  uniform float u_vmax;
  uniform float u_nodata;
  uniform vec3 u_bg;
  uniform float u_bgAlpha;
  uniform float u_transparentBg;
  uniform float u_vignetteIntensity;
  uniform float u_maxRange;
  uniform float u_transitionAlpha;

  float bilinear(vec4 v, vec2 f) {
    float top = mix(v.x, v.y, f.y);
    float bot = mix(v.z, v.w, f.y);
    return mix(top, bot, f.x);
  }

  void main() {
    float nd = u_nodata + 0.5;
    bool allNodata = (v_values4.x <= nd && v_values4.y <= nd &&
                      v_values4.z <= nd && v_values4.w <= nd);
    if (allNodata) {
      if (u_transparentBg > 0.5) {
        gl_FragColor = vec4(0.0, 0.0, 0.0, 0.0);
      } else {
        gl_FragColor = vec4(u_bg, u_bgAlpha);
      }
      return;
    }
    // Replace nodata corners with average of valid corners
    float sum = 0.0;
    float cnt = 0.0;
    vec4 v = v_values4;
    if (v.x > nd) { sum += v.x; cnt += 1.0; }
    if (v.y > nd) { sum += v.y; cnt += 1.0; }
    if (v.z > nd) { sum += v.z; cnt += 1.0; }
    if (v.w > nd) { sum += v.w; cnt += 1.0; }
    float avg = sum / max(cnt, 1.0);
    if (v.x <= nd) v.x = avg;
    if (v.y <= nd) v.y = avg;
    if (v.z <= nd) v.z = avg;
    if (v.w <= nd) v.w = avg;

    float val = bilinear(v, v_polar);
    float t = clamp((val - u_vmin) / (u_vmax - u_vmin), 0.0, 1.0);
    vec4 color = texture2D(u_colormap, vec2(t, 0.5));
    // Vignette
    if (u_vignetteIntensity > 0.0 && u_maxRange > 0.0) {
      float dist = length(v_pos) / u_maxRange;
      float vig = 1.0 - u_vignetteIntensity * smoothstep(0.4, 1.0, dist);
      color.rgb *= vig;
    }
    color.a *= u_transitionAlpha;
    gl_FragColor = color;
  }
`;

// --- Contour (second pass) shader ---

const CONTOUR_VERTEX_SHADER = `
  attribute vec2 a_position;
  attribute float a_value;
  uniform mat3 u_matrix;
  varying float v_value;
  void main() {
    vec3 pos = u_matrix * vec3(a_position, 1.0);
    gl_Position = vec4(pos.xy, 0.0, 1.0);
    v_value = a_value;
  }
`;

const CONTOUR_FRAGMENT_SHADER = `
  #extension GL_OES_standard_derivatives : enable
  precision mediump float;
  varying float v_value;
  uniform float u_vmin;
  uniform float u_vmax;
  uniform float u_nodata;
  uniform float u_contourInterval;
  uniform vec3 u_contourColor;
  uniform float u_contourWidth;
  void main() {
    if (v_value <= u_nodata + 0.5) {
      discard;
    }
    float interval = u_contourInterval;
    float wrapped = mod(v_value, interval);
    float dv = fwidth(v_value);
    float halfWidth = u_contourWidth * 0.5;
    float edge = smoothstep(halfWidth + dv, max(halfWidth - dv, 0.0),
                            min(wrapped, interval - wrapped));
    if (edge < 0.01) {
      discard;
    }
    gl_FragColor = vec4(u_contourColor, edge * 0.85);
  }
`;

// ─── Ring overlay shader (draws range rings + crosshair) ────────────────

const RING_VERTEX_SHADER = `
  attribute vec2 a_position;
  uniform mat3 u_matrix;
  void main() {
    vec3 pos = u_matrix * vec3(a_position, 1.0);
    gl_Position = vec4(pos.xy, 0.0, 1.0);
  }
`;

const RING_FRAGMENT_SHADER = `
  precision mediump float;
  uniform vec4 u_color;
  void main() {
    gl_FragColor = u_color;
  }
`;

// ─── Helper: compile & link a WebGL program ─────────────────────────────

function _compileShader(gl: WebGLRenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type)!;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.error('[PPI] Shader compile error:', gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function _linkProgram(gl: WebGLRenderingContext, vsSrc: string, fsSrc: string): WebGLProgram | null {
  const vs = _compileShader(gl, gl.VERTEX_SHADER, vsSrc);
  const fs = _compileShader(gl, gl.FRAGMENT_SHADER, fsSrc);
  if (!vs || !fs) return null;
  const prog = gl.createProgram()!;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.error('[PPI] Program link failed:', gl.getProgramInfoLog(prog));
    return null;
  }
  return prog;
}

// ─── Uniform location bags ──────────────────────────────────────────────

interface SharpLocs {
  aPosition: number;
  aValue: number;
  uMatrix: WebGLUniformLocation | null;
  uVmin: WebGLUniformLocation | null;
  uVmax: WebGLUniformLocation | null;
  uNodata: WebGLUniformLocation | null;
  uColormap: WebGLUniformLocation | null;
  uBg: WebGLUniformLocation | null;
  uBgAlpha: WebGLUniformLocation | null;
  uTransparentBg: WebGLUniformLocation | null;
  uVignetteIntensity: WebGLUniformLocation | null;
  uMaxRange: WebGLUniformLocation | null;
  uTransitionAlpha: WebGLUniformLocation | null;
}

interface SmoothLocs {
  aPosition: number;
  aPolar: number;
  aValues4: number;
  uMatrix: WebGLUniformLocation | null;
  uVmin: WebGLUniformLocation | null;
  uVmax: WebGLUniformLocation | null;
  uNodata: WebGLUniformLocation | null;
  uColormap: WebGLUniformLocation | null;
  uBg: WebGLUniformLocation | null;
  uBgAlpha: WebGLUniformLocation | null;
  uTransparentBg: WebGLUniformLocation | null;
  uVignetteIntensity: WebGLUniformLocation | null;
  uMaxRange: WebGLUniformLocation | null;
  uTransitionAlpha: WebGLUniformLocation | null;
}

interface ContourLocs {
  aPosition: number;
  aValue: number;
  uMatrix: WebGLUniformLocation | null;
  uVmin: WebGLUniformLocation | null;
  uVmax: WebGLUniformLocation | null;
  uNodata: WebGLUniformLocation | null;
  uContourInterval: WebGLUniformLocation | null;
  uContourColor: WebGLUniformLocation | null;
  uContourWidth: WebGLUniformLocation | null;
}

// ─── WebGL PPI Renderer ─────────────────────────────────────────────────

export class PPIRenderer {
  private gl: WebGLRenderingContext | null = null;
  private canvas: HTMLCanvasElement | null = null;

  // --- Shader programs ---
  private sharpProgram: WebGLProgram | null = null;
  private sharpLocs: SharpLocs | null = null;
  private smoothProgram: WebGLProgram | null = null;
  private smoothLocs: SmoothLocs | null = null;
  private contourProgram: WebGLProgram | null = null;
  private contourLocs: ContourLocs | null = null;
  private ringProgram: WebGLProgram | null = null;
  private ruMatrix: WebGLUniformLocation | null = null;
  private ruColor: WebGLUniformLocation | null = null;
  private raPosition = -1;

  // --- Sharp (flat) buffers ---
  private posBuffer: WebGLBuffer | null = null;
  private valBuffer: WebGLBuffer | null = null;
  private vertexCount = 0;

  // --- Smooth (bilinear) buffers ---
  private smoothPosBuffer: WebGLBuffer | null = null;
  private smoothPolarBuffer: WebGLBuffer | null = null;
  private smoothVal4Buffer: WebGLBuffer | null = null;
  private smoothVertexCount = 0;

  // --- Transition: secondary buffer set for cross-fade ---
  private transPosBuffer: WebGLBuffer | null = null;
  private transValBuffer: WebGLBuffer | null = null;
  private transVertexCount = 0;
  private transSmoothPosBuffer: WebGLBuffer | null = null;
  private transSmoothPolarBuffer: WebGLBuffer | null = null;
  private transSmoothVal4Buffer: WebGLBuffer | null = null;
  private transSmoothVertexCount = 0;
  private transitionActive = false;
  private transitionAlpha = 1.0;
  private transitionStart = 0;
  private transitionDuration = 0;
  private transitionRafId = 0;

  // Colormap / ring / view state
  private colormapTex: WebGLTexture | null = null;
  private currentCmapName = '';
  private ringBuffer: WebGLBuffer | null = null;
  private ringVertexCount = 0;
  private scale = 1;
  private panX = 0;
  private panY = 0;
  private maxRange = 1;

  // Mesh worker
  private meshWorker: Worker | null = null;
  private meshWorkerReady = false;
  private pendingMeshResolve: ((result: MeshWorkerOutput) => void) | null = null;

  // Feature toggles
  private interpolationMode: 'sharp' | 'smooth' = 'sharp';
  private contoursEnabled = false;
  private contourInterval = 5;
  private contourColor: [number, number, number] = [1, 1, 1];
  private contourWidthVal = 1.5;
  private vignetteIntensity = 0.0;
  private transparentBg = false;

  // FPS counter
  private fpsEnabled = false;
  private fpsFrameCount = 0;
  private fpsLastTime = 0;
  private fpsValue = 0;
  private fpsCallback: ((fps: number) => void) | null = null;

  // ANGLE_instanced_arrays (stored for future instanced rendering)
  private instancedExt: ANGLE_instanced_arrays | null = null;

  // Stored render params for re-render during transitions
  private lastVmin = 0;
  private lastVmax = 1;

  // ─── Initialization ───────────────────────────────────────────────────

  init(canvas: HTMLCanvasElement): boolean {
    this.canvas = canvas;
    const gl = canvas.getContext('webgl', {
      antialias: true,
      premultipliedAlpha: false,
      preserveDrawingBuffer: true,
      alpha: true,
    });
    if (!gl) return false;
    this.gl = gl;

    // Extensions
    this.instancedExt = gl.getExtension('ANGLE_instanced_arrays');
    gl.getExtension('OES_standard_derivatives'); // for fwidth() in contour shader

    // --- Build shader programs ---

    // Sharp program (enhanced version of the original)
    const sharpProg = _linkProgram(gl, VERTEX_SHADER_SHARP, FRAGMENT_SHADER_SHARP);
    if (!sharpProg) return false;
    this.sharpProgram = sharpProg;
    this.sharpLocs = {
      aPosition: gl.getAttribLocation(sharpProg, 'a_position'),
      aValue: gl.getAttribLocation(sharpProg, 'a_value'),
      uMatrix: gl.getUniformLocation(sharpProg, 'u_matrix'),
      uVmin: gl.getUniformLocation(sharpProg, 'u_vmin'),
      uVmax: gl.getUniformLocation(sharpProg, 'u_vmax'),
      uNodata: gl.getUniformLocation(sharpProg, 'u_nodata'),
      uColormap: gl.getUniformLocation(sharpProg, 'u_colormap'),
      uBg: gl.getUniformLocation(sharpProg, 'u_bg'),
      uBgAlpha: gl.getUniformLocation(sharpProg, 'u_bgAlpha'),
      uTransparentBg: gl.getUniformLocation(sharpProg, 'u_transparentBg'),
      uVignetteIntensity: gl.getUniformLocation(sharpProg, 'u_vignetteIntensity'),
      uMaxRange: gl.getUniformLocation(sharpProg, 'u_maxRange'),
      uTransitionAlpha: gl.getUniformLocation(sharpProg, 'u_transitionAlpha'),
    };

    // Smooth program (bilinear interpolation)
    const smoothProg = _linkProgram(gl, VERTEX_SHADER_SMOOTH, FRAGMENT_SHADER_SMOOTH);
    if (smoothProg) {
      this.smoothProgram = smoothProg;
      this.smoothLocs = {
        aPosition: gl.getAttribLocation(smoothProg, 'a_position'),
        aPolar: gl.getAttribLocation(smoothProg, 'a_polar'),
        aValues4: gl.getAttribLocation(smoothProg, 'a_values4'),
        uMatrix: gl.getUniformLocation(smoothProg, 'u_matrix'),
        uVmin: gl.getUniformLocation(smoothProg, 'u_vmin'),
        uVmax: gl.getUniformLocation(smoothProg, 'u_vmax'),
        uNodata: gl.getUniformLocation(smoothProg, 'u_nodata'),
        uColormap: gl.getUniformLocation(smoothProg, 'u_colormap'),
        uBg: gl.getUniformLocation(smoothProg, 'u_bg'),
        uBgAlpha: gl.getUniformLocation(smoothProg, 'u_bgAlpha'),
        uTransparentBg: gl.getUniformLocation(smoothProg, 'u_transparentBg'),
        uVignetteIntensity: gl.getUniformLocation(smoothProg, 'u_vignetteIntensity'),
        uMaxRange: gl.getUniformLocation(smoothProg, 'u_maxRange'),
        uTransitionAlpha: gl.getUniformLocation(smoothProg, 'u_transitionAlpha'),
      };
    }

    // Contour program
    const contourProg = _linkProgram(gl, CONTOUR_VERTEX_SHADER, CONTOUR_FRAGMENT_SHADER);
    if (contourProg) {
      this.contourProgram = contourProg;
      this.contourLocs = {
        aPosition: gl.getAttribLocation(contourProg, 'a_position'),
        aValue: gl.getAttribLocation(contourProg, 'a_value'),
        uMatrix: gl.getUniformLocation(contourProg, 'u_matrix'),
        uVmin: gl.getUniformLocation(contourProg, 'u_vmin'),
        uVmax: gl.getUniformLocation(contourProg, 'u_vmax'),
        uNodata: gl.getUniformLocation(contourProg, 'u_nodata'),
        uContourInterval: gl.getUniformLocation(contourProg, 'u_contourInterval'),
        uContourColor: gl.getUniformLocation(contourProg, 'u_contourColor'),
        uContourWidth: gl.getUniformLocation(contourProg, 'u_contourWidth'),
      };
    }

    // Ring program
    const ringProg = _linkProgram(gl, RING_VERTEX_SHADER, RING_FRAGMENT_SHADER);
    if (ringProg) {
      this.ringProgram = ringProg;
      this.raPosition = gl.getAttribLocation(ringProg, 'a_position');
      this.ruMatrix = gl.getUniformLocation(ringProg, 'u_matrix');
      this.ruColor = gl.getUniformLocation(ringProg, 'u_color');
    }

    // --- Create all buffers ---
    this.posBuffer = gl.createBuffer();
    this.valBuffer = gl.createBuffer();
    this.smoothPosBuffer = gl.createBuffer();
    this.smoothPolarBuffer = gl.createBuffer();
    this.smoothVal4Buffer = gl.createBuffer();
    this.transPosBuffer = gl.createBuffer();
    this.transValBuffer = gl.createBuffer();
    this.transSmoothPosBuffer = gl.createBuffer();
    this.transSmoothPolarBuffer = gl.createBuffer();
    this.transSmoothVal4Buffer = gl.createBuffer();
    this.ringBuffer = gl.createBuffer();
    this.colormapTex = gl.createTexture();

    // Dark background matching the app theme
    gl.clearColor(15 / 255, 15 / 255, 26 / 255, 1);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    // Initialize mesh worker
    this.initMeshWorker();

    return true;
  }

  // ─── Public API: feature toggles ──────────────────────────────────────

  /** Switch between 'sharp' (flat per-gate) and 'smooth' (bilinear interpolation). */
  setInterpolation(mode: 'sharp' | 'smooth'): void {
    this.interpolationMode = mode;
  }

  /** Enable/disable contour lines with configurable interval and color. */
  setContours(enabled: boolean, interval: number = 5, color: [number, number, number] = [1, 1, 1]): void {
    this.contoursEnabled = enabled;
    this.contourInterval = interval;
    this.contourColor = color;
  }

  /** Set contour line width (default 1.5). */
  setContourWidth(width: number): void {
    this.contourWidthVal = width;
  }

  /** Set vignette intensity (0 = off, 1 = strong darkening at edges). */
  setVignette(intensity: number): void {
    this.vignetteIntensity = Math.max(0, Math.min(1, intensity));
  }

  /** Enable/disable transparent background (no-data regions become see-through). */
  setTransparentBackground(enabled: boolean): void {
    this.transparentBg = enabled;
  }

  /** Enable/disable FPS counter. Provide a callback to receive FPS updates. */
  setFpsCounter(enabled: boolean, callback?: (fps: number) => void): void {
    this.fpsEnabled = enabled;
    this.fpsCallback = callback ?? null;
    if (enabled) {
      this.fpsFrameCount = 0;
      this.fpsLastTime = performance.now();
    }
  }

  /** Get the current FPS value (0 if counter is disabled). */
  getFps(): number {
    return this.fpsEnabled ? this.fpsValue : 0;
  }

  // ─── Mesh worker ──────────────────────────────────────────────────────

  /** Spawn the mesh computation Web Worker. */
  private initMeshWorker(): void {
    try {
      this.meshWorker = new Worker(
        new URL('../workers/meshWorker.ts', import.meta.url),
        { type: 'module' }
      );
      this.meshWorkerReady = true;

      this.meshWorker.onmessage = (e: MessageEvent<MeshWorkerOutput>) => {
        if (this.pendingMeshResolve) {
          this.pendingMeshResolve(e.data);
          this.pendingMeshResolve = null;
        }
      };

      this.meshWorker.onerror = (err) => {
        console.warn('[PPI] Mesh worker error, falling back to main thread:', err);
        this.meshWorkerReady = false;
      };
    } catch (err) {
      console.warn('[PPI] Could not create mesh worker, using main thread:', err);
      this.meshWorkerReady = false;
    }
  }

  /** Send mesh work to the worker and return a promise for the result. */
  private buildMeshInWorker(input: MeshWorkerInput): Promise<MeshWorkerOutput> {
    return new Promise((resolve) => {
      this.pendingMeshResolve = resolve;

      const azCopy = new Float32Array(input.azimuth);
      const rngCopy = new Float32Array(input.range);
      const valCopy = new Float32Array(input.values);

      const msg: MeshWorkerInput = {
        azimuth: azCopy,
        range: rngCopy,
        values: valCopy,
        nAz: input.nAz,
        nRange: input.nRange,
      };

      this.meshWorker!.postMessage(msg, [
        azCopy.buffer,
        rngCopy.buffer,
        valCopy.buffer,
      ]);
    });
  }

  /** Synchronous fallback: build sharp mesh on main thread using trig LUT. */
  private buildMeshSync(entry: SweepDataEntry): { positions: Float32Array; vals: Float32Array; vertexCount: number; buildTimeMs: number } {
    const t0 = performance.now();
    const { azimuth, range, values, nAz, nRange } = entry;

    const positions: number[] = [];
    const vals: number[] = [];

    for (let ai = 0; ai < nAz; ai++) {
      const az0 = azimuth[ai];
      const az1 = ai + 1 < nAz ? azimuth[ai + 1] : azimuth[0] + 360;

      const sin0 = fastSin(az0), cos0 = fastCos(az0);
      const sin1 = fastSin(az1), cos1 = fastCos(az1);

      for (let ri = 0; ri < nRange - 1; ri++) {
        const v = values[ai * nRange + ri];
        if (v <= -9998) continue;

        const r0 = range[ri];
        const r1 = range[ri + 1];

        const x00 = r0 * sin0, y00 = r0 * cos0;
        const x01 = r1 * sin0, y01 = r1 * cos0;
        const x10 = r0 * sin1, y10 = r0 * cos1;
        const x11 = r1 * sin1, y11 = r1 * cos1;

        positions.push(x00, y00, x01, y01, x10, y10);
        vals.push(v, v, v);
        positions.push(x01, y01, x11, y11, x10, y10);
        vals.push(v, v, v);
      }
    }

    return {
      positions: new Float32Array(positions),
      vals: new Float32Array(vals),
      vertexCount: vals.length,
      buildTimeMs: performance.now() - t0,
    };
  }

  // ─── Smooth mesh builder ──────────────────────────────────────────────

  /** Build bilinear-interpolation mesh (smooth mode) on main thread. */
  private buildSmoothMesh(entry: SweepDataEntry): {
    positions: Float32Array;
    polars: Float32Array;
    values4: Float32Array;
    vertexCount: number;
  } {
    const { azimuth, range, values, nAz, nRange } = entry;
    const posArr: number[] = [];
    const polArr: number[] = [];
    const v4Arr: number[] = [];

    for (let ai = 0; ai < nAz; ai++) {
      const ai1 = (ai + 1) % nAz;
      const az0 = azimuth[ai];
      const az1 = ai + 1 < nAz ? azimuth[ai + 1] : azimuth[0] + 360;

      const sin0 = fastSin(az0), cos0 = fastCos(az0);
      const sin1 = fastSin(az1), cos1 = fastCos(az1);

      for (let ri = 0; ri < nRange - 1; ri++) {
        const v00 = values[ai * nRange + ri];
        const v01 = values[ai * nRange + ri + 1];
        const v10 = values[ai1 * nRange + ri];
        const v11 = values[ai1 * nRange + ri + 1];

        if (v00 <= -9998 && v01 <= -9998 && v10 <= -9998 && v11 <= -9998) continue;

        const r0 = range[ri];
        const r1 = range[ri + 1];

        const x00 = r0 * sin0, y00 = r0 * cos0;
        const x01 = r1 * sin0, y01 = r1 * cos0;
        const x10 = r0 * sin1, y10 = r0 * cos1;
        const x11 = r1 * sin1, y11 = r1 * cos1;

        // Triangle 1: (0,0) (0,1) (1,0)
        posArr.push(x00, y00, x01, y01, x10, y10);
        polArr.push(0, 0, 0, 1, 1, 0);
        v4Arr.push(v00, v01, v10, v11);
        v4Arr.push(v00, v01, v10, v11);
        v4Arr.push(v00, v01, v10, v11);

        // Triangle 2: (0,1) (1,1) (1,0)
        posArr.push(x01, y01, x11, y11, x10, y10);
        polArr.push(0, 1, 1, 1, 1, 0);
        v4Arr.push(v00, v01, v10, v11);
        v4Arr.push(v00, v01, v10, v11);
        v4Arr.push(v00, v01, v10, v11);
      }
    }

    const vertexCount = posArr.length / 2;
    return {
      positions: new Float32Array(posArr),
      polars: new Float32Array(polArr),
      values4: new Float32Array(v4Arr),
      vertexCount,
    };
  }

  // ─── Sweep data upload ────────────────────────────────────────────────

  /**
   * Generate triangle mesh from polar data and upload to GPU.
   * Uses the render cache when available, otherwise computes via Web Worker
   * (falling back to main thread if the worker is unavailable).
   * Also builds the smooth mesh for bilinear interpolation mode.
   */
  uploadSweepData(entry: SweepDataEntry): void {
    this.maxRange = entry.maxRange;

    // --- Sharp mesh (original pipeline, cache-aware) ---

    const cached = renderCache.get(entry.variable, entry.sweep);
    if (cached) {
      this.uploadMeshToGPU(cached.positions, cached.vals, cached.vertexCount);
      this.buildRingGeometry(entry.maxRange);
      perfStats.update(s => ({ ...s, meshBuildTimeMs: 0 }));
    } else if (this.meshWorkerReady && this.meshWorker) {
      const transferStart = performance.now();
      this.buildMeshInWorker({
        azimuth: entry.azimuth,
        range: entry.range,
        values: entry.values,
        nAz: entry.nAz,
        nRange: entry.nRange,
      }).then((result) => {
        const transferTime = performance.now() - transferStart - result.buildTimeMs;
        perfStats.update(s => ({
          ...s,
          meshBuildTimeMs: result.buildTimeMs,
          dataTransferTimeMs: Math.max(0, transferTime),
        }));

        renderCache.put(entry.variable, entry.sweep, {
          positions: result.positions,
          vals: result.vals,
          vertexCount: result.vertexCount,
          key: `${entry.variable}:${entry.sweep}`,
        });

        this.uploadMeshToGPU(result.positions, result.vals, result.vertexCount);
        this.buildRingGeometry(entry.maxRange);
      });
    } else {
      const result = this.buildMeshSync(entry);
      perfStats.update(s => ({ ...s, meshBuildTimeMs: result.buildTimeMs, dataTransferTimeMs: 0 }));

      renderCache.put(entry.variable, entry.sweep, {
        positions: result.positions,
        vals: result.vals,
        vertexCount: result.vertexCount,
        key: `${entry.variable}:${entry.sweep}`,
      });

      this.uploadMeshToGPU(result.positions, result.vals, result.vertexCount);
      this.buildRingGeometry(entry.maxRange);
    }

    // --- Smooth mesh (always built on main thread for now) ---
    this.uploadSmoothMesh(entry);
  }

  /** Upload pre-computed sharp mesh arrays to WebGL buffers. */
  private uploadMeshToGPU(positions: Float32Array, vals: Float32Array, vertexCount: number): void {
    const gl = this.gl;
    if (!gl) return;

    this.vertexCount = vertexCount;

    gl.bindBuffer(gl.ARRAY_BUFFER, this.posBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.valBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, vals, gl.STATIC_DRAW);
  }

  /** Build and upload the smooth (bilinear) mesh to GPU. */
  private uploadSmoothMesh(entry: SweepDataEntry): void {
    const gl = this.gl;
    if (!gl || !this.smoothProgram) return;

    const mesh = this.buildSmoothMesh(entry);
    this.smoothVertexCount = mesh.vertexCount;

    gl.bindBuffer(gl.ARRAY_BUFFER, this.smoothPosBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.positions, gl.STATIC_DRAW);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.smoothPolarBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.polars, gl.STATIC_DRAW);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.smoothVal4Buffer);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.values4, gl.STATIC_DRAW);
  }

  /** Upload a colormap texture by name. */
  setColormap(cmapName: string): void {
    if (cmapName === this.currentCmapName) return;
    if (!COLORMAP_DATA[cmapName]) return;
    this.currentCmapName = cmapName;

    const gl = this.gl!;
    const rgba = buildColormapRGBA(cmapName);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.colormapTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, rgba);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  }

  private buildRingGeometry(maxRange: number): void {
    const gl = this.gl;
    if (!gl || !this.ringProgram) return;

    const verts: number[] = [];
    const segments = 128;

    const ringSpacingKm = maxRange > 300000 ? 100 : maxRange > 100000 ? 50 : maxRange > 30000 ? 25 : 10;
    const ringSpacing = ringSpacingKm * 1000;
    const numRings = Math.floor(maxRange / ringSpacing);

    for (let ri = 1; ri <= numRings; ri++) {
      const r = ri * ringSpacing;
      for (let s = 0; s < segments; s++) {
        const a0 = (s / segments) * Math.PI * 2;
        const a1 = ((s + 1) / segments) * Math.PI * 2;
        verts.push(r * Math.sin(a0), r * Math.cos(a0));
        verts.push(r * Math.sin(a1), r * Math.cos(a1));
      }
    }

    // Crosshair (N-S and E-W lines)
    verts.push(0, -maxRange, 0, maxRange);
    verts.push(-maxRange, 0, maxRange, 0);

    this.ringVertexCount = verts.length / 2;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ringBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.STATIC_DRAW);
  }

  // ─── View matrix ──────────────────────────────────────────────────────

  private buildMatrix(): number[] {
    const w = this.canvas!.width;
    const h = this.canvas!.height;
    const mr = this.maxRange;
    const aspect = w / h;
    const base = this.scale / mr;
    const sx = aspect >= 1 ? base / aspect : base;
    const sy = aspect >= 1 ? base : base * aspect;
    const tx = this.panX * sx;
    const ty = this.panY * sy;
    return [sx, 0, 0, 0, sy, 0, tx, ty, 1];
  }

  // ─── Shared uniform setter ────────────────────────────────────────────

  private setCommonUniforms(
    gl: WebGLRenderingContext,
    locs: {
      uBg: WebGLUniformLocation | null; uBgAlpha: WebGLUniformLocation | null;
      uTransparentBg: WebGLUniformLocation | null; uVignetteIntensity: WebGLUniformLocation | null;
      uMaxRange: WebGLUniformLocation | null; uTransitionAlpha: WebGLUniformLocation | null;
      uVmin: WebGLUniformLocation | null; uVmax: WebGLUniformLocation | null;
      uNodata: WebGLUniformLocation | null; uColormap: WebGLUniformLocation | null;
      uMatrix: WebGLUniformLocation | null;
    },
    matrix: number[],
    vmin: number,
    vmax: number,
    alpha: number,
  ): void {
    gl.uniformMatrix3fv(locs.uMatrix, false, matrix);
    gl.uniform1f(locs.uVmin, vmin);
    gl.uniform1f(locs.uVmax, vmax);
    gl.uniform1f(locs.uNodata, -9999);
    gl.uniform3f(locs.uBg, 15 / 255, 15 / 255, 26 / 255);
    gl.uniform1f(locs.uBgAlpha, this.transparentBg ? 0.0 : 1.0);
    gl.uniform1f(locs.uTransparentBg, this.transparentBg ? 1.0 : 0.0);
    gl.uniform1f(locs.uVignetteIntensity, this.vignetteIntensity);
    gl.uniform1f(locs.uMaxRange, this.maxRange);
    gl.uniform1f(locs.uTransitionAlpha, alpha);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.colormapTex);
    gl.uniform1i(locs.uColormap, 0);
  }

  // ─── Render ───────────────────────────────────────────────────────────

  /** Render the PPI with overlays. */
  render(vmin: number, vmax: number): void {
    const gl = this.gl;
    if (!gl || !this.sharpProgram) return;
    if (this.vertexCount === 0 && this.smoothVertexCount === 0 && !this.transitionActive) return;

    const renderStart = performance.now();
    this.lastVmin = vmin;
    this.lastVmax = vmax;

    // FPS tracking
    if (this.fpsEnabled) {
      this.fpsFrameCount++;
      const now = performance.now();
      if (now - this.fpsLastTime >= 1000) {
        this.fpsValue = Math.round(this.fpsFrameCount * 1000 / (now - this.fpsLastTime));
        this.fpsFrameCount = 0;
        this.fpsLastTime = now;
        if (this.fpsCallback) this.fpsCallback(this.fpsValue);
      }
    }

    const w = this.canvas!.width;
    const h = this.canvas!.height;
    gl.viewport(0, 0, w, h);

    if (this.transparentBg) {
      gl.clearColor(0, 0, 0, 0);
    } else {
      gl.clearColor(15 / 255, 15 / 255, 26 / 255, 1);
    }
    gl.clear(gl.COLOR_BUFFER_BIT);

    const matrix = this.buildMatrix();

    // --- If transition is active, draw old data first with (1 - alpha) ---
    if (this.transitionActive) {
      const oldAlpha = 1.0 - this.transitionAlpha;
      this.drawPPIPass(gl, matrix, vmin, vmax, oldAlpha,
        this.transPosBuffer!, this.transValBuffer!, this.transVertexCount,
        this.transSmoothPosBuffer!, this.transSmoothPolarBuffer!, this.transSmoothVal4Buffer!, this.transSmoothVertexCount,
      );
    }

    // --- Draw current data ---
    const alpha = this.transitionActive ? this.transitionAlpha : 1.0;
    if (this.vertexCount > 0 || this.smoothVertexCount > 0) {
      this.drawPPIPass(gl, matrix, vmin, vmax, alpha,
        this.posBuffer!, this.valBuffer!, this.vertexCount,
        this.smoothPosBuffer!, this.smoothPolarBuffer!, this.smoothVal4Buffer!, this.smoothVertexCount,
      );
    }

    // --- Contour pass ---
    if (this.contoursEnabled && this.contourProgram && this.contourLocs && this.vertexCount > 0) {
      this.drawContourPass(gl, matrix, vmin, vmax);
    }

    // --- Range rings ---
    if (this.ringProgram && this.ringVertexCount > 0) {
      gl.useProgram(this.ringProgram);
      gl.uniformMatrix3fv(this.ruMatrix, false, matrix);
      gl.uniform4f(this.ruColor, 1, 1, 1, 0.15);

      gl.bindBuffer(gl.ARRAY_BUFFER, this.ringBuffer);
      gl.enableVertexAttribArray(this.raPosition);
      gl.vertexAttribPointer(this.raPosition, 2, gl.FLOAT, false, 0, 0);

      gl.drawArrays(gl.LINES, 0, this.ringVertexCount);
      gl.disableVertexAttribArray(this.raPosition);
    }

    perfStats.update(s => ({ ...s, renderTimeMs: performance.now() - renderStart }));
  }

  /** Draw PPI triangles using either sharp or smooth mode. */
  private drawPPIPass(
    gl: WebGLRenderingContext,
    matrix: number[],
    vmin: number,
    vmax: number,
    alpha: number,
    sharpPosBuf: WebGLBuffer,
    sharpValBuf: WebGLBuffer,
    sharpCount: number,
    smoothPosBuf: WebGLBuffer,
    smoothPolarBuf: WebGLBuffer,
    smoothVal4Buf: WebGLBuffer,
    smoothCount: number,
  ): void {
    const useSmooth = this.interpolationMode === 'smooth'
      && this.smoothProgram && this.smoothLocs && smoothCount > 0;

    if (useSmooth) {
      const locs = this.smoothLocs!;
      gl.useProgram(this.smoothProgram!);
      this.setCommonUniforms(gl, locs, matrix, vmin, vmax, alpha);

      gl.bindBuffer(gl.ARRAY_BUFFER, smoothPosBuf);
      gl.enableVertexAttribArray(locs.aPosition);
      gl.vertexAttribPointer(locs.aPosition, 2, gl.FLOAT, false, 0, 0);

      gl.bindBuffer(gl.ARRAY_BUFFER, smoothPolarBuf);
      gl.enableVertexAttribArray(locs.aPolar);
      gl.vertexAttribPointer(locs.aPolar, 2, gl.FLOAT, false, 0, 0);

      gl.bindBuffer(gl.ARRAY_BUFFER, smoothVal4Buf);
      gl.enableVertexAttribArray(locs.aValues4);
      gl.vertexAttribPointer(locs.aValues4, 4, gl.FLOAT, false, 0, 0);

      gl.drawArrays(gl.TRIANGLES, 0, smoothCount);

      gl.disableVertexAttribArray(locs.aPosition);
      gl.disableVertexAttribArray(locs.aPolar);
      gl.disableVertexAttribArray(locs.aValues4);
    } else if (sharpCount > 0) {
      const locs = this.sharpLocs!;
      gl.useProgram(this.sharpProgram!);
      this.setCommonUniforms(gl, locs, matrix, vmin, vmax, alpha);

      gl.bindBuffer(gl.ARRAY_BUFFER, sharpPosBuf);
      gl.enableVertexAttribArray(locs.aPosition);
      gl.vertexAttribPointer(locs.aPosition, 2, gl.FLOAT, false, 0, 0);

      gl.bindBuffer(gl.ARRAY_BUFFER, sharpValBuf);
      gl.enableVertexAttribArray(locs.aValue);
      gl.vertexAttribPointer(locs.aValue, 1, gl.FLOAT, false, 0, 0);

      gl.drawArrays(gl.TRIANGLES, 0, sharpCount);

      gl.disableVertexAttribArray(locs.aPosition);
      gl.disableVertexAttribArray(locs.aValue);
    }
  }

  /** Draw contour lines as a second pass over the sharp mesh. */
  private drawContourPass(gl: WebGLRenderingContext, matrix: number[], vmin: number, vmax: number): void {
    const locs = this.contourLocs!;
    gl.useProgram(this.contourProgram!);

    gl.uniformMatrix3fv(locs.uMatrix, false, matrix);
    gl.uniform1f(locs.uVmin, vmin);
    gl.uniform1f(locs.uVmax, vmax);
    gl.uniform1f(locs.uNodata, -9999);
    gl.uniform1f(locs.uContourInterval, this.contourInterval);
    gl.uniform3fv(locs.uContourColor, this.contourColor);
    gl.uniform1f(locs.uContourWidth, this.contourWidthVal);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.posBuffer);
    gl.enableVertexAttribArray(locs.aPosition);
    gl.vertexAttribPointer(locs.aPosition, 2, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.valBuffer);
    gl.enableVertexAttribArray(locs.aValue);
    gl.vertexAttribPointer(locs.aValue, 1, gl.FLOAT, false, 0, 0);

    gl.drawArrays(gl.TRIANGLES, 0, this.vertexCount);

    gl.disableVertexAttribArray(locs.aPosition);
    gl.disableVertexAttribArray(locs.aValue);
  }

  // ─── Animation: cross-fade transition ─────────────────────────────────

  /**
   * Smoothly cross-fade from the current sweep to new sweep data.
   * @param newData  The new sweep data to transition to.
   * @param duration Transition duration in milliseconds (default 500).
   */
  transitionToSweep(newData: SweepDataEntry, duration: number = 500): void {
    const gl = this.gl;
    if (!gl) return;

    // Cancel any in-progress transition
    if (this.transitionRafId) {
      cancelAnimationFrame(this.transitionRafId);
      this.transitionRafId = 0;
    }

    // Swap buffer references: current -> transition (old), upload new -> current
    let tmp: WebGLBuffer | null;
    let tmpN: number;

    tmp = this.posBuffer;
    this.posBuffer = this.transPosBuffer;
    this.transPosBuffer = tmp;

    tmp = this.valBuffer;
    this.valBuffer = this.transValBuffer;
    this.transValBuffer = tmp;

    tmpN = this.vertexCount;
    this.vertexCount = 0;
    this.transVertexCount = tmpN;

    tmp = this.smoothPosBuffer;
    this.smoothPosBuffer = this.transSmoothPosBuffer;
    this.transSmoothPosBuffer = tmp;

    tmp = this.smoothPolarBuffer;
    this.smoothPolarBuffer = this.transSmoothPolarBuffer;
    this.transSmoothPolarBuffer = tmp;

    tmp = this.smoothVal4Buffer;
    this.smoothVal4Buffer = this.transSmoothVal4Buffer;
    this.transSmoothVal4Buffer = tmp;

    tmpN = this.smoothVertexCount;
    this.smoothVertexCount = 0;
    this.transSmoothVertexCount = tmpN;

    // Upload new data into the (now-swapped) current buffers
    this.uploadSweepData(newData);

    // Start transition animation
    this.transitionActive = true;
    this.transitionAlpha = 0;
    this.transitionStart = performance.now();
    this.transitionDuration = duration;

    const animate = () => {
      const elapsed = performance.now() - this.transitionStart;
      const t = Math.min(elapsed / this.transitionDuration, 1.0);
      // Smooth ease-in-out (smoothstep)
      this.transitionAlpha = t * t * (3 - 2 * t);

      this.render(this.lastVmin, this.lastVmax);

      if (t < 1.0) {
        this.transitionRafId = requestAnimationFrame(animate);
      } else {
        this.transitionActive = false;
        this.transitionAlpha = 1.0;
        this.transitionRafId = 0;
      }
    };

    this.transitionRafId = requestAnimationFrame(animate);
  }

  // ─── View / resize / lifecycle ────────────────────────────────────────

  setView(scale: number, panX: number, panY: number): void {
    this.scale = scale;
    this.panX = panX;
    this.panY = panY;
  }

  resize(width: number, height: number): void {
    if (!this.canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
  }

  destroy(): void {
    const gl = this.gl;
    if (!gl) return;

    if (this.transitionRafId) {
      cancelAnimationFrame(this.transitionRafId);
      this.transitionRafId = 0;
    }

    const buffers = [
      this.posBuffer, this.valBuffer,
      this.smoothPosBuffer, this.smoothPolarBuffer, this.smoothVal4Buffer,
      this.transPosBuffer, this.transValBuffer,
      this.transSmoothPosBuffer, this.transSmoothPolarBuffer, this.transSmoothVal4Buffer,
      this.ringBuffer,
    ];
    for (const buf of buffers) {
      if (buf) gl.deleteBuffer(buf);
    }

    if (this.colormapTex) gl.deleteTexture(this.colormapTex);

    const programs = [this.sharpProgram, this.smoothProgram, this.contourProgram, this.ringProgram];
    for (const prog of programs) {
      if (prog) gl.deleteProgram(prog);
    }

    this.gl = null;

    if (this.meshWorker) {
      this.meshWorker.terminate();
      this.meshWorker = null;
      this.meshWorkerReady = false;
    }
  }

  getMaxRange(): number {
    return this.maxRange;
  }

  private compileShader(type: number, source: string): WebGLShader | null {
    return _compileShader(this.gl!, type, source);
  }
}
