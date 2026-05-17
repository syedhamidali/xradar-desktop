"""Cloud data access: AWS NEXRAD Level II (archive + real-time) and NEXRAD ARCO.

All S3 listing/downloading uses boto3 (synchronous, no asyncio) to avoid
event-loop conflicts when called from run_in_executor thread-pool threads.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_NEXRAD_L2_BUCKET = "unidata-nexrad-level2"
_NEXRAD_ARCO_BUCKET = "nexrad-arco"


def _s3():
    """Create an anonymous boto3 S3 client (no asyncio, thread-safe)."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    return boto3.client(
        "s3",
        config=Config(signature_version=UNSIGNED),
        region_name="us-east-1",
    )


# ── Station registry ──────────────────────────────────────────────────────────
_NEXRAD_STATIONS = [
    "KABR","KABX","KAKQ","KAMA","KAMX","KAPX","KARX","KATX","KBBX","KBGM",
    "KBHX","KBIS","KBLX","KBMX","KBOX","KBRO","KBUF","KBYX","KCAE","KCBW",
    "KCBX","KCCX","KCLE","KCLX","KCRP","KCXX","KCYS","KDAX","KDDC","KDFX",
    "KDGX","KDIX","KDLH","KDMX","KDOX","KDTX","KDVN","KDYX","KEAX","KEMX",
    "KENX","KEOX","KEPZ","KESX","KEVX","KEWX","KEYX","KFCX","KFDR","KFDX",
    "KFFC","KFSD","KFSX","KFTG","KFWS","KGGW","KGJX","KGLD","KGRB","KGRK",
    "KGRR","KGSP","KGWX","KGYX","KHDX","KHGX","KHNX","KHPX","KHTX","KICT",
    "KICX","KILN","KILX","KIND","KINX","KIWA","KIWX","KJAX","KJGX","KJKL",
    "KLBB","KLCH","KLGX","KLIX","KLNX","KLOT","KLRX","KLSX","KLTX","KLVX",
    "KLWX","KLZK","KMAF","KMAX","KMBX","KMHX","KMKX","KMLB","KMOB","KMPX",
    "KMQT","KMRX","KMSX","KMTX","KMUX","KMVX","KMXX","KNKX","KNQA","KOAX",
    "KOHX","KOKX","KOTX","KPAH","KPBZ","KPDT","KPOE","KPUX","KRAX","KRGX",
    "KRIW","KRLX","KRTX","KSFX","KSGF","KSHV","KSJT","KSOX","KSRX","KTBW",
    "KTFX","KTLH","KTLX","KTWX","KTYX","KUDX","KUEX","KVAX","KVBX","KVNX",
    "KVTX","KVWX","KYUX","PABC","PACG","PAEC","PAHG","PAIH","PAKC","PAPD",
    "PGUA","PHKI","PHKM","PHMO","PHWA","TJUA",
]

_ARCO_STATIONS: set[str] | None = None
_ARCO_REPOS: dict[str, Any] = {}  # station_upper → icechunk.Repository (cached)


def list_nexrad_stations() -> list[dict[str, str]]:
    """Return full NEXRAD station list, marking which have ARCO data."""
    arco = _get_arco_stations()
    return [{"id": s, "arco": s in arco} for s in _NEXRAD_STATIONS]


def _get_arco_stations() -> set[str]:
    global _ARCO_STATIONS
    if _ARCO_STATIONS is not None:
        return _ARCO_STATIONS
    try:
        client = _s3()
        response = client.list_objects_v2(
            Bucket=_NEXRAD_ARCO_BUCKET,
            Delimiter="/",
            MaxKeys=1000,
        )
        _ARCO_STATIONS = {
            cp["Prefix"].rstrip("/")
            for cp in response.get("CommonPrefixes", [])
        }
        if not _ARCO_STATIONS:
            _ARCO_STATIONS = {"KLOT"}
    except Exception as exc:
        logger.warning("Could not list ARCO stations: %s", exc)
        _ARCO_STATIONS = {"KLOT"}
    return _ARCO_STATIONS


# ── NEXRAD Level II: archive ──────────────────────────────────────────────────

def list_nexrad_l2_files(station: str, date: str) -> list[dict[str, Any]]:
    """List Level II files for a station on a given date (YYYY-MM-DD)."""
    dt = datetime.strptime(date, "%Y-%m-%d")
    prefix = f"{dt.year}/{dt.month:02d}/{dt.day:02d}/{station.upper()}/"

    client = _s3()
    paginator = client.get_paginator("list_objects_v2")

    files = []
    try:
        for page in paginator.paginate(Bucket=_NEXRAD_L2_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"].split("/")[-1]
                if name.endswith("_MDM") or not name.startswith(station.upper()):
                    continue
                files.append({
                    "name": name,
                    "path": f"s3://{_NEXRAD_L2_BUCKET}/{obj['Key']}",
                    "size": obj.get("Size", 0),
                    "last_modified": str(obj.get("LastModified", "")),
                })
    except Exception as exc:
        raise RuntimeError(f"Could not list files for {station} on {date}: {exc}") from exc

    return sorted(files, key=lambda f: f["name"])


# ── NEXRAD Level II: real-time ────────────────────────────────────────────────

def get_latest_nexrad_l2(station: str) -> dict[str, Any]:
    """Return the most recent Level II file for a station (today or yesterday)."""
    now = datetime.now(timezone.utc)
    for delta_days in (0, 1):
        day = now - timedelta(days=delta_days)
        date_str = day.strftime("%Y-%m-%d")
        try:
            files = list_nexrad_l2_files(station, date_str)
            if files:
                return files[-1]
        except Exception:
            continue
    raise RuntimeError(f"No recent Level II files found for {station}")


# ── NEXRAD ARCO (icechunk / Zarr v3) ─────────────────────────────────────────

def _open_icechunk_session(station: str):
    """Open a readonly icechunk session for a NEXRAD station.

    The Repository object is cached per station so the expensive S3 manifest
    download only happens once per process lifetime.  Each call still creates
    a fresh readonly_session("main") so callers see the latest snapshot.
    """
    global _ARCO_REPOS
    try:
        import icechunk
    except ImportError as exc:
        raise RuntimeError("icechunk is required: pip install icechunk") from exc
    station_upper = station.upper()
    if station_upper not in _ARCO_REPOS:
        storage = icechunk.s3_storage(
            bucket=_NEXRAD_ARCO_BUCKET,
            prefix=station_upper,
            region="us-east-1",
            anonymous=True,
        )
        try:
            logger.info("Opening icechunk repository for %s (first time, may take ~30s)…", station_upper)
            _ARCO_REPOS[station_upper] = icechunk.Repository.open(storage)
            logger.info("icechunk repository for %s cached", station_upper)
        except Exception as exc:
            raise RuntimeError(f"Could not open ARCO store for {station}: {exc}") from exc
    return _ARCO_REPOS[station_upper].readonly_session("main")


def list_arco_scans(station: str, limit: int = 50) -> list[dict[str, Any]]:
    """List available VCP types in the ARCO icechunk store for a station.

    Each 'scan' corresponds to one Volume Coverage Pattern (VCP) type,
    e.g. VCP-12, VCP-34. Each VCP contains all sweeps and all historical
    scan times along the vcp_time dimension.
    """
    import zarr

    session = _open_icechunk_session(station)
    try:
        root = zarr.open_group(session.store, mode="r")
        vcps = sorted(
            k for k in root.keys()
            if not k.startswith(".") and isinstance(root[k], zarr.Group)
        )
    except Exception as exc:
        raise RuntimeError(f"Could not list ARCO VCPs for {station}: {exc}") from exc

    station_upper = station.upper()
    return [
        {"key": vcp, "path": f"icechunk://nexrad-arco/{station_upper}/{vcp}"}
        for vcp in vcps
    ]
