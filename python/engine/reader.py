"""Radar file reader using xradar with auto-format detection."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

# Map of xradar opener functions keyed by format name.
# Each entry: (format_label, opener_function_name, supported_extensions)
_FORMAT_TABLE: list[tuple[str, str, set[str]]] = [
    ("nexrad_level2", "open_nexradlevel2_datatree", {".gz", ".bz2", ""}),
    ("cfradial1", "open_cfradial1_datatree", {".nc", ".ncf", ".cdf"}),
    ("odim", "open_odim_datatree", {".h5", ".hdf5", ".hdf"}),
    ("gamic", "open_gamic_datatree", {".h5", ".hdf5", ".hdf"}),
    ("iris", "open_iris_datatree", {".RAW", ".raw"}),
    ("furuno", "open_furuno_datatree", {".scn", ".scnx"}),
    ("rainbow", "open_rainbow_datatree", {".vol", ".azi"}),
    ("datamet", "open_datamet_datatree", set()),
    ("hpl", "open_hpl_datatree", set()),
    ("metek", "open_metek_datatree", set()),
]


def _get_opener(format_name: str):
    """Dynamically import the xradar opener for the given format."""
    import xradar as xd

    # xradar exposes openers at xradar.io.<name>
    io_mod = xd.io
    func_name = None
    for label, fname, _ in _FORMAT_TABLE:
        if label == format_name:
            func_name = fname
            break
    if func_name is None:
        raise ValueError(f"Unknown format: {format_name}")
    opener = getattr(io_mod, func_name, None)
    if opener is None:
        raise ImportError(
            f"xradar.io.{func_name} not available in installed xradar version"
        )
    return opener


def _sniff_magic(path: str) -> str | None:
    """Read the first few bytes of a file to identify its format via magic bytes.

    Returns a format name from _FORMAT_TABLE or None if unrecognised.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(16)
    except OSError:
        return None

    if not header:
        return None

    # HDF5: starts with \x89HDF\r\n\x1a\n
    # NetCDF4 also uses HDF5 container, so check extension to disambiguate.
    if header[:8] == b"\x89HDF\r\n\x1a\n":
        ext = Path(path).suffix.lower()
        if ext in (".nc", ".ncf", ".cdf"):
            return "cfradial1"
        # Default to ODIM for .h5/.hdf5; fallback handles GAMIC
        return "odim"

    # NetCDF classic: starts with 'CDF'
    if header[:3] == b"CDF":
        return "cfradial1"

    # NEXRAD Level 2: starts with 'AR2V' or 'ARCH'
    if header[:4] in (b"AR2V", b"ARCH"):
        return "nexrad_level2"

    # IRIS/Sigmet: struct header with product_hdr magic (27 as int16 LE at byte 0)
    if len(header) >= 4 and header[0:2] == b"\x1b\x00":
        return "iris"

    # Gzip-compressed: could be NEXRAD
    if header[:2] == b"\x1f\x8b":
        return "nexrad_level2"

    # Bzip2-compressed: could be NEXRAD
    if header[:2] == b"BZ":
        return "nexrad_level2"

    return None


def _guess_formats_by_extension(path: str) -> list[str]:
    """Return a priority-ordered list of format names to try based on file extension."""
    p = Path(path)
    suffixes = {s.lower() for s in p.suffixes}
    name_upper = p.name.upper()

    ordered: list[str] = []

    # 1. Magic byte sniffing — fastest, most reliable
    magic_fmt = _sniff_magic(path)
    if magic_fmt:
        ordered.append(magic_fmt)

    # 2. Extension-based hints (deduped against magic result)
    seen = set(ordered)

    if (not p.suffix or ".gz" in suffixes or ".bz2" in suffixes) and "nexrad_level2" not in seen:
        ordered.append("nexrad_level2")
        seen.add("nexrad_level2")

    if suffixes & {".nc", ".ncf", ".cdf"} and "cfradial1" not in seen:
        ordered.append("cfradial1")
        seen.add("cfradial1")

    if suffixes & {".h5", ".hdf5", ".hdf"}:
        for fmt in ("odim", "gamic"):
            if fmt not in seen:
                ordered.append(fmt)
                seen.add(fmt)

    if (suffixes & {".raw"} or "RAW" in name_upper) and "iris" not in seen:
        ordered.append("iris")
        seen.add("iris")

    if suffixes & {".scn", ".scnx"} and "furuno" not in seen:
        ordered.append("furuno")
        seen.add("furuno")

    if suffixes & {".vol", ".azi"} and "rainbow" not in seen:
        ordered.append("rainbow")
        seen.add("rainbow")

    # 3. If nothing matched, try all formats
    if not ordered:
        ordered = [fmt for fmt, _, _ in _FORMAT_TABLE]

    # 4. Append remaining formats as fallbacks (deduped)
    for fmt, _, _ in _FORMAT_TABLE:
        if fmt not in seen:
            ordered.append(fmt)
            seen.add(fmt)

    return ordered


def _open_generic_netcdf(path: str) -> xr.DataTree:
    """Fallback opener for non-standard NetCDF radar files (e.g. IMD/IRIS-origin).

    These files are single-sweep NetCDFs with variables like Z, V, W, T
    and coordinate arrays like radialAzim, radialElev, gateSize.
    We wrap them into a DataTree that looks like xradar output.
    """
    ds = xr.open_dataset(path)

    # Identify 2D float moment variables
    moment_vars = [v for v in ds.data_vars if ds[v].ndim == 2 and ds[v].dtype.kind == "f"]
    if not moment_vars:
        raise ValueError("No 2D float variables found — not a radar file")

    # Find azimuth and range coordinates
    azimuth = None
    for candidate in ("radialAzim", "azimuth", "Azimuth", "azi"):
        if candidate in ds:
            azimuth = ds[candidate].values.astype(np.float64)
            break

    # Build range array from gate metadata or existing coord
    range_arr = None
    for candidate in ("range", "Range", "distance"):
        if candidate in ds and ds[candidate].ndim == 1:
            range_arr = ds[candidate].values.astype(np.float64)
            break
    if range_arr is None and "gateSize" in ds and "firstGateRange" in ds:
        gate_size = float(ds["gateSize"].values)
        first_range = float(ds["firstGateRange"].values)
        n_gates = ds[moment_vars[0]].shape[-1]
        range_arr = np.arange(n_gates, dtype=np.float64) * gate_size + first_range

    # Get elevation
    elevation = None
    for candidate in ("elevationAngle", "elevation", "Elevation", "elev"):
        if candidate in ds:
            val = ds[candidate].values
            elevation = float(val.flat[0]) if val.ndim > 0 else float(val)
            break

    # Build a single-sweep dataset with xradar-compatible coords
    sweep_ds_vars = {}
    dims_2d = ds[moment_vars[0]].dims  # e.g. ('radial', 'bin')
    dim_az, dim_rng = dims_2d[0], dims_2d[1]

    for v in moment_vars:
        sweep_ds_vars[v] = xr.DataArray(
            ds[v].values,
            dims=("azimuth", "range"),
        )

    coords: dict[str, Any] = {}
    if azimuth is not None:
        coords["azimuth"] = ("azimuth", azimuth)
    if range_arr is not None:
        coords["range"] = ("range", range_arr)

    sweep_ds = xr.Dataset(sweep_ds_vars, coords=coords)
    if elevation is not None:
        sweep_ds.attrs["sweep_fixed_angle"] = elevation

    # Preserve useful global attrs
    for attr_key in ("siteLat", "siteLon", "siteAlt", "title", "Conventions", "history"):
        if attr_key in ds.attrs:
            sweep_ds.attrs[attr_key] = ds.attrs[attr_key]

    ds.close()

    # Wrap in DataTree (xarray >= 2024 API: children dict)
    sweep_node = xr.DataTree(dataset=sweep_ds, name="sweep_0")
    root = xr.DataTree(children={"sweep_0": sweep_node}, name="root")
    return root


class RadarReader:
    """Opens radar files via xradar and provides access to sweep data and metadata."""

    def __init__(self) -> None:
        self._dtree: xr.DataTree | None = None
        self._path: str | None = None
        self._format: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_file(self, path: str) -> dict[str, Any]:
        """Open a radar file, auto-detecting the format.

        Returns the schema dict (same shape as ``get_schema()``).

        Raises
        ------
        FileNotFoundError
            If *path* does not exist on the local filesystem.
        ValueError
            If no xradar backend can open the file or the path is invalid.
        """
        if not path or not isinstance(path, str):
            raise ValueError("Path must be a non-empty string")

        p = Path(path)

        # Directory traversal protection: require absolute paths and ensure
        # the resolved path doesn't escape via '..' tricks.
        if not p.is_absolute():
            raise ValueError(
                f"Only absolute file paths are accepted, got: {path}"
            )
        resolved = p.resolve()
        # Ensure the resolved path doesn't differ from what we'd expect
        # (e.g. symlink escape).  The key check is that '..' doesn't appear
        # in the resolved components — Path.resolve() normalises those away,
        # so if the original contains '..' but resolves differently, that's
        # acceptable as long as the resolved target exists.
        if ".." in resolved.parts:
            raise ValueError(
                f"Path contains disallowed '..' components: {path}"
            )

        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")

        formats_to_try = _guess_formats_by_extension(path)
        last_exc: Exception | None = None

        for fmt in formats_to_try:
            try:
                opener = _get_opener(fmt)
                logger.info("Trying format '%s' for %s", fmt, path)
                dtree = opener(path)
                self._dtree = dtree
                self._path = path
                self._format = fmt
                logger.info("Opened %s as %s", path, fmt)
                return self.get_schema()
            except (ValueError, TypeError, KeyError, OSError, ImportError) as exc:
                logger.debug("Format '%s' failed for %s: %s", fmt, path, exc)
                last_exc = exc
                continue
            except Exception as exc:
                logger.debug(
                    "Format '%s' failed unexpectedly for %s: %s", fmt, path, exc
                )
                last_exc = exc
                continue

        # Last resort: try generic NetCDF (IMD/IRIS-origin single-sweep files, etc.)
        try:
            logger.info("Trying generic NetCDF fallback for %s", path)
            dtree = _open_generic_netcdf(path)
            self._dtree = dtree
            self._path = path
            self._format = "generic_netcdf"
            logger.info("Opened %s as generic NetCDF", path)
            return self.get_schema()
        except Exception as exc:
            logger.debug("Generic NetCDF fallback failed: %s", exc)

        raise ValueError(
            f"Could not open {path} with any supported format. "
            f"Last error: {last_exc}"
        )

    def get_schema(self) -> dict[str, Any]:
        """Extract metadata from the currently-open datatree.

        Returns a dict with keys:
            variables  - list of variable name strings
            dimensions - dict mapping dim name to size
            attributes - dict of global attributes
            sweeps     - list of dicts with sweep index and elevation
        """
        self._ensure_open()
        assert self._dtree is not None

        dtree = self._dtree

        # Collect sweep nodes — xradar stores sweeps as children named
        # "sweep_0", "sweep_1", etc.
        sweep_nodes = self._sweep_nodes()

        # Extract variables and dimensions from first sweep only —
        # they're identical across sweeps, so no need to materialise all 23.
        # Only include actual radar moment variables (2D: azimuth × range),
        # excluding scalar metadata like sweep_mode, sweep_number, etc.
        variables: set[str] = set()
        dimensions: dict[str, int] = {}
        if sweep_nodes:
            ds0 = sweep_nodes[0].to_dataset()
            for v in ds0.data_vars:
                da = ds0[v]
                # Radar moments are 2D (azimuth, range) float arrays
                if da.ndim == 2 and da.dtype.kind == "f":
                    variables.add(str(v))
            for dim, size in ds0.sizes.items():
                dimensions[str(dim)] = int(size)

        # Extract sweep elevations cheaply. sweep_fixed_angle can be:
        # - a DataTree node attr, a Dataset attr, or a scalar data variable.
        # We read the scalar without loading the full elevation array.
        sweeps: list[dict[str, Any]] = []
        for idx, node in enumerate(sweep_nodes):
            elevation: float | None = None
            try:
                ds = node.to_dataset()
                if "sweep_fixed_angle" in ds.attrs:
                    elevation = float(ds.attrs["sweep_fixed_angle"])
                elif "sweep_fixed_angle" in ds:
                    # Scalar variable — fast to read
                    elevation = float(ds["sweep_fixed_angle"].values)
                elif "elevation" in ds:
                    # Fallback: use first elevation value (avoid full array mean)
                    elevation = float(ds["elevation"].values.flat[0])
            except Exception:
                pass
            sweeps.append({"index": idx, "elevation": elevation})

        # Global attributes — from root node
        root_attrs: dict[str, Any] = {}
        try:
            root_ds = dtree.to_dataset()
            for k, v in root_ds.attrs.items():
                root_attrs[str(k)] = _serializable(v)
        except (ValueError, KeyError, AttributeError) as exc:
            logger.debug("Could not extract root attributes: %s", exc)

        return {
            "variables": sorted(variables),
            "dimensions": dimensions,
            "attributes": root_attrs,
            "sweeps": sweeps,
        }

    def get_sweep_data(self, sweep: int, variable: str) -> xr.DataArray:
        """Return the DataArray for a specific sweep index and variable.

        Parameters
        ----------
        sweep : int
            Zero-based sweep index.
        variable : str
            Variable name (e.g. ``"DBZH"``, ``"reflectivity"``).

        Returns
        -------
        xr.DataArray
        """
        self._ensure_open()
        nodes = self._sweep_nodes()
        if sweep < 0 or sweep >= len(nodes):
            raise IndexError(
                f"Sweep index {sweep} out of range (0..{len(nodes) - 1})"
            )
        ds = nodes[sweep].to_dataset()
        if variable not in ds.data_vars:
            available = sorted(str(v) for v in ds.data_vars)
            raise KeyError(
                f"Variable '{variable}' not in sweep {sweep}. "
                f"Available: {available}"
            )
        return ds[variable]

    @property
    def datatree(self) -> xr.DataTree | None:
        """Access the underlying DataTree (may be None if no file is open)."""
        return self._dtree

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_open(self) -> None:
        if self._dtree is None:
            raise RuntimeError("No file is currently open. Call open_file() first.")

    def _sweep_nodes(self) -> list[xr.DataTree]:
        """Return child nodes that represent sweeps, sorted by name."""
        assert self._dtree is not None
        nodes: list[xr.DataTree] = []
        for name in sorted(self._dtree.children):
            if name.startswith("sweep_"):
                nodes.append(self._dtree[name])
        return nodes


def _serializable(value: Any) -> Any:
    """Convert numpy types to plain Python for JSON serialization."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
