# xradar-desktop

A lightweight, cross-platform desktop application for radar data visualization and analysis, built on [xradar](https://github.com/openradar/xradar), [xarray](https://xarray.dev/), and [Dask](https://www.dask.org/).

## Features

- **Multi-format support** -- NEXRAD Level II, ODIM H5, CfRadial, IRIS/Sigmet, Rainbow, Furuno, IMD
- **GPU-accelerated rendering** -- deck.gl + Mapbox GL for 60fps pan/zoom with radar overlays
- **Lazy, out-of-core processing** -- Dask-backed xarray for large datasets without memory limits
- **Publication-quality export** -- High-DPI raster (Datashader) + vector overlays (Cairo) to PNG/SVG/PDF
- **Dual-pol products** -- ZDR, KDP, HID, and custom dual-polarization dashboards
- **Cross-sections & profiles** -- RHI slices, vertical profiles, and time-series analysis
- **QC pipelines** -- Despeckle, velocity dealiasing, and custom filtering
- **Cloud access** -- Open files from S3, THREDDS, or OPeNDAP via fsspec
- **Small footprint** -- ~15MB installer (Tauri, not Electron)

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop shell | Tauri 2.0 (Rust) |
| Frontend | Svelte 5 |
| Map / Radar view | deck.gl + Mapbox GL |
| Data transfer | WebSocket + Apache Arrow IPC |
| Data engine | xradar + xarray + Dask |
| Rendering | Datashader + Cairo + Pillow |

## Prerequisites

- [Node.js](https://nodejs.org/) >= 20
- [Rust](https://www.rust-lang.org/tools/install) (stable)
- Python >= 3.10
- System dependencies:
  - **macOS**: Xcode Command Line Tools, `brew install cairo pkg-config`
  - **Linux**: `sudo apt install libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf libgtk-3-dev libcairo2-dev pkg-config`
  - **Windows**: WebView2 (usually pre-installed on Windows 10+)

## Getting Started

```bash
# Clone the repo
git clone https://github.com/syedhamidali/xradar-desktop.git
cd xradar-desktop

# Install frontend dependencies
npm install

# Set up Python virtual environment and install sidecar dependencies
cd python
python3 -m venv .venv
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\activate       # Windows
pip install -e ".[dev]"
cd ..

# Run in development mode
npm run tauri dev
```

> **Note:** A virtual environment is required on modern Linux distributions
> (Ubuntu 23.04+, Debian 12+, Fedora 38+) due to [PEP 668](https://peps.python.org/pep-0668/).
> The Tauri app automatically detects and uses `python/.venv` if it exists.

## Project Structure

```
xradar-desktop/
├── src/                    # Svelte frontend
│   ├── App.svelte
│   └── lib/
│       ├── components/     # UI components (RadarViewer, DataInspector, etc.)
│       ├── stores/         # Reactive state management
│       ├── utils/          # WebSocket client, Arrow IPC, colormaps
│       └── workers/        # Web workers for binary processing
├── src-tauri/              # Rust / Tauri shell
│   └── src/main.rs
├── python/                 # Python sidecar
│   ├── server.py           # WebSocket server
│   ├── engine/             # xradar processing engine
│   └── tests/
├── package.json
└── vite.config.ts
```

## Development

```bash
# Activate the Python venv first
source python/.venv/bin/activate

# Lint Python
ruff check --config ruff.toml python/
ruff format --config ruff.toml python/

# Type-check frontend
npx svelte-check --tsconfig ./tsconfig.json
npx tsc --noEmit

# Run Python tests
cd python && python -m pytest tests/ -v

# Build for production
npm run tauri build
```

## License

MIT
