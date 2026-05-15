"""Cloud data access: AWS NEXRAD Level II (archive + real-time) and NEXRAD ARCO."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import s3fs

logger = logging.getLogger(__name__)

# ── S3 buckets ───────────────────────────────────────────────────────────────
_NEXRAD_L2_BUCKET = "noaa-nexrad-level2"
_NEXRAD_ARCO_BUCKET = "nexrad-arco"

# Anonymous S3 filesystem (no AWS account required)
_s3 = s3fs.S3FileSystem(anon=True)


# ── Station helpers ───────────────────────────────────────────────────────────

def list_nexrad_stations() -> list[dict[str, str]]:
    """Return NEXRAD stations that have ARCO data, plus the full CONUS list."""
    # ARCO currently has KLOT; full list for Level II
    arco_stations = _list_arco_stations()
    all_l2 = _list_l2_stations_today()
    merged: dict[str, dict] = {}
    for s in all_l2:
        merged[s] = {"id": s, "arco": s in arco_stations}
    for s in arco_stations:
        merged[s] = {"id": s, "arco": True}
    return sorted(merged.values(), key=lambda x: x["id"])


def _list_arco_stations() -> set[str]:
    try:
        entries = _s3.ls(f"{_NEXRAD_ARCO_BUCKET}/", detail=False)
        return {e.split("/")[-1] for e in entries if not e.endswith(".json")}
    except Exception as exc:
        logger.warning("Could not list ARCO stations: %s", exc)
        return {"KLOT"}


def _list_l2_stations_today() -> list[str]:
    now = datetime.now(timezone.utc)
    prefix = f"{_NEXRAD_L2_BUCKET}/{now.year}/{now.month:02d}/{now.day:02d}/"
    try:
        entries = _s3.ls(prefix, detail=False)
        return sorted({e.rstrip("/").split("/")[-1] for e in entries})
    except Exception as exc:
        logger.warning("Could not list today's L2 stations: %s", exc)
        return []


# ── NEXRAD Level II: archive ──────────────────────────────────────────────────

def list_nexrad_l2_files(
    station: str, date: str
) -> list[dict[str, Any]]:
    """List Level II files for a station on a given date (YYYY-MM-DD)."""
    dt = datetime.strptime(date, "%Y-%m-%d")
    prefix = f"{_NEXRAD_L2_BUCKET}/{dt.year}/{dt.month:02d}/{dt.day:02d}/{station.upper()}/"
    try:
        entries = _s3.ls(prefix, detail=True)
    except Exception as exc:
        raise RuntimeError(f"Could not list files for {station} on {date}: {exc}") from exc

    files = []
    for e in entries:
        name = e["name"].split("/")[-1]
        # Skip MDM metadata files
        if name.endswith("_MDM") or not name.startswith(station.upper()):
            continue
        files.append({
            "name": name,
            "path": f"s3://{e['name']}",
            "size": e.get("size", 0),
            "last_modified": str(e.get("LastModified", "")),
        })
    return sorted(files, key=lambda f: f["name"])


def open_nexrad_l2_path(s3_path: str) -> str:
    """Return an fsspec-compatible URL for a Level II file (passed to xradar)."""
    # xradar can open s3:// paths via fsspec with anon=True via storage_options
    return s3_path


# ── NEXRAD Level II: real-time (latest scan) ─────────────────────────────────

def get_latest_nexrad_l2(station: str) -> dict[str, Any]:
    """Return the most recent Level II file for a station (today or yesterday)."""
    now = datetime.now(timezone.utc)
    for delta_days in (0, 1):
        from datetime import timedelta
        day = now - timedelta(days=delta_days)
        date_str = day.strftime("%Y-%m-%d")
        try:
            files = list_nexrad_l2_files(station, date_str)
            if files:
                return files[-1]
        except Exception:
            continue
    raise RuntimeError(f"No recent Level II files found for {station}")


# ── NEXRAD ARCO (Zarr v3 / Icechunk) ─────────────────────────────────────────

def list_arco_scans(station: str, limit: int = 50) -> list[dict[str, Any]]:
    """List available volume scan times in the ARCO store for a station."""
    try:
        import zarr
    except ImportError:
        raise RuntimeError("zarr is required for ARCO access: pip install zarr icechunk")

    store_url = f"s3://{_NEXRAD_ARCO_BUCKET}/{station.upper()}"
    try:
        store = zarr.storage.FsspecStore(store_url, fs=_s3)
        root = zarr.open_group(store, mode="r")
        scans = []
        for key in sorted(root.keys()):
            scans.append({"key": key, "path": f"{store_url}/{key}"})
        return scans[-limit:]
    except Exception as exc:
        raise RuntimeError(f"Could not open ARCO store for {station}: {exc}") from exc


def open_arco_scan(station: str, scan_key: str) -> dict[str, Any]:
    """
    Open a single ARCO scan as an xradar DataTree and return its schema.
    Returns a dict with path info so the reader can open it.
    """
    store_url = f"s3://{_NEXRAD_ARCO_BUCKET}/{station.upper()}/{scan_key}"
    return {
        "type": "arco",
        "path": store_url,
        "station": station.upper(),
        "scan_key": scan_key,
    }
