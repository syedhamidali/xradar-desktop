# xradar-desktop

A lightweight, cross-platform desktop application for radar data visualization and analysis, built on [xradar](https://github.com/openradar/xradar), [xarray](https://xarray.dev/), and [Dask](https://www.dask.org/).

## Features

- **Multi-format support** — NEXRAD Level II, ODIM H5, CfRadial, IRIS/Sigmet, Rainbow, Furuno, IMD (gzip-wrapped files handled automatically)
- **2D PPI viewer** — GPU-accelerated pan/zoom with map background, geo overlays, range rings, and colorbar
- **3D volume renderer** — WebGL2 polar ray-marching shader; orbit, zoom, and pan around the full radar volume; transfer function editor for opacity/color mapping
- **2D→3D box-clip** — Draw a selection box in the 2D view to isolate a region; the 3D view clips to that column and re-centers the camera automatically
- **Lazy, out-of-core processing** — Dask-backed xarray; large datasets load without memory limits
- **Publication-quality export** — High-DPI raster (Datashader) + vector overlays (Cairo) to PNG/SVG/PDF
- **Dual-pol products** — ZDR, KDP, HID, and custom dual-polarization dashboards
- **Cross-sections & profiles** — RHI slices, vertical profiles, and time-series analysis
- **QC pipelines** — Despeckle, velocity dealiasing, and custom filtering
- **Cloud access** — Open NEXRAD Level II directly from `s3://noaa-nexrad-level2` or ARCO data via boto3/icechunk
- **Small footprint** — ~15 MB installer (Tauri 2, not Electron)

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop shell | Tauri 2.0 (Rust) |
| Frontend | Svelte 5 |
| 2D radar view | WebGL2 (custom PPI renderer) |
| 3D volume view | WebGL2 polar ray-marching shader |
| Map overlays | Mapbox GL / deck.gl |
| Data transfer | WebSocket + Apache Arrow IPC |
| Data engine | xradar + xarray + Dask |
| Rendering | Datashader + Cairo + Pillow |
| Cloud data | boto3 + icechunk + s3fs + fsspec |

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Node.js | >= 20 | [nodejs.org](https://nodejs.org/) |
| Rust (stable) | latest | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| Tauri CLI | ^2 | `cargo install tauri-cli@^2` |
| Python | >= 3.10 | [python.org](https://www.python.org/) |

### System libraries

**macOS**
```bash
xcode-select --install
brew install cairo pkg-config
```

**Ubuntu / Debian**
```bash
sudo apt install libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev \
                 patchelf libgtk-3-dev libcairo2-dev pkg-config
```

**Windows**
WebView2 is pre-installed on Windows 10+. No extra steps required.

## Installation

```bash
# 1. Clone
git clone https://github.com/syedhamidali/xradar-desktop.git
cd xradar-desktop

# 2. Frontend dependencies
npm install

# 3. Python sidecar — create a virtual environment and install
cd python
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

pip install -e ".[dev]"
cd ..

# 4. Launch in development mode
npm run tauri dev
```

> **Note:** A virtual environment is required on Ubuntu 23.04+, Debian 12+, and Fedora 38+
> due to [PEP 668](https://peps.python.org/pep-0668/).
> The app auto-detects and uses `python/.venv` when present.

## Build

```bash
# Production installer (creates platform-native .dmg / .deb / .msi)
npm run tauri build
```

Installers are written to `src-tauri/target/release/bundle/`.

## Project Structure

```
xradar-desktop/
├── src/                        # Svelte frontend
│   ├── App.svelte
│   └── lib/
│       ├── components/         # RadarViewer (2D), RadarViewer3D, DataInspector, ...
│       ├── stores/             # Reactive state (radarData, volumeSelect, ...)
│       ├── utils/              # WebSocket client, Arrow IPC, PPI renderer, colormaps
│       └── workers/            # Web workers for binary processing
├── src-tauri/                  # Rust / Tauri shell
│   └── src/main.rs
├── python/                     # Python sidecar (WebSocket engine)
│   ├── server.py               # WebSocket entry point
│   ├── engine/                 # reader, renderer, cloud, arrow_bridge
│   └── tests/
├── package.json
└── vite.config.ts
```

## Development

```bash
# Activate the Python venv first
source python/.venv/bin/activate   # macOS / Linux

# Lint & format Python
ruff check --config ruff.toml python/
ruff format --config ruff.toml python/

# Type-check frontend
npx svelte-check --tsconfig ./tsconfig.json
npx tsc --noEmit

# Run Python tests
cd python && python -m pytest tests/ -v

# Build frontend only (no Tauri)
npm run build
```

## Using the 3D View

1. Open a radar file and select a variable — sweeps start loading automatically.
2. Switch to the **3D** tab; the volume renders as each sweep arrives (lowest elevation first).
3. **Orbit**: drag. **Zoom**: scroll or `+`/`-`. **Pan**: Shift+drag. **Reset**: `R`.
4. Adjust **Step** (ray-march resolution) and **Opacity** from the toolbar, or open the **TF** editor to tune the transfer function.
5. To focus on a storm cell, switch back to 2D, click the **box-select** button (dashed rectangle icon), draw a box around the region, then switch to 3D — the view clips and re-centers on that column.

## License

MIT
