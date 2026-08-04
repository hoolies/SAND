"""Local Plotly.js bundle: serve from disk, refresh from CDN when online."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("sand.plotly")

VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "plotly"
MANIFEST_PATH = VENDOR_DIR / "manifest.json"
BUNDLE_PATH = VENDOR_DIR / "plotly.min.js"
DEFAULT_VERSION = "2.35.3"
CDN_TEMPLATE = "https://cdn.plot.ly/plotly-{version}.min.js"
NPM_META_URL = "https://registry.npmjs.org/plotly.js"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_lock = threading.Lock()
_last_status: dict[str, Any] = {
    "version": DEFAULT_VERSION,
    "updated": False,
    "checked": False,
    "online": None,
    "message": "not checked yet",
}


@dataclass(frozen=True)
class PlotlyManifest:
    version: str
    source: str
    major_pin: int = 2

    @classmethod
    def load(cls, path: Path | None = None) -> PlotlyManifest:
        path = path or MANIFEST_PATH
        if not path.exists():
            return cls(
                version=DEFAULT_VERSION,
                source=CDN_TEMPLATE.format(version=DEFAULT_VERSION),
                major_pin=2,
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            version=str(raw.get("version") or DEFAULT_VERSION),
            source=str(raw.get("source") or CDN_TEMPLATE.format(version=raw.get("version") or DEFAULT_VERSION)),
            major_pin=int(raw.get("major_pin") or 2),
        )

    def dump(self, path: Path | None = None) -> None:
        path = path or MANIFEST_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"version": self.version, "source": self.source, "major_pin": self.major_pin},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def parse_version(version: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(version.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def version_gt(a: str, b: str) -> bool:
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return False
    return pa > pb


def bundle_ready() -> bool:
    return BUNDLE_PATH.is_file() and BUNDLE_PATH.stat().st_size > 100_000


def current_status() -> dict[str, Any]:
    with _lock:
        status = dict(_last_status)
    manifest = PlotlyManifest.load()
    status["version"] = manifest.version
    status["bundle_ready"] = bundle_ready()
    status["path"] = str(BUNDLE_PATH)
    return status


def _set_status(**kwargs: Any) -> None:
    with _lock:
        _last_status.update(kwargs)


def ensure_local_bundle(*, timeout: float = 20.0) -> PlotlyManifest:
    """Ensure plotly.min.js exists. Downloads the pinned version if missing and online."""
    manifest = PlotlyManifest.load()
    if bundle_ready():
        _set_status(version=manifest.version, message="using local bundle", online=None)
        return manifest

    url = CDN_TEMPLATE.format(version=manifest.version)
    logger.info("Plotly bundle missing; downloading %s", url)
    try:
        _download(url, BUNDLE_PATH, timeout=timeout)
    except Exception as exc:
        _set_status(
            version=manifest.version,
            checked=True,
            online=False,
            updated=False,
            message=f"bundle missing and download failed: {exc}",
        )
        raise
    manifest = PlotlyManifest(
        version=manifest.version,
        source=url,
        major_pin=manifest.major_pin,
    )
    manifest.dump()
    _set_status(
        version=manifest.version,
        checked=True,
        online=True,
        updated=True,
        message=f"downloaded initial bundle {manifest.version}",
    )
    return manifest


def latest_version_for_major(major: int, *, timeout: float = 8.0) -> str | None:
    """Return newest plotly.js version on npm matching ``major.*`` (best-effort)."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(NPM_META_URL)
        resp.raise_for_status()
        data = resp.json()
    versions = data.get("versions") or {}
    best: str | None = None
    for ver in versions:
        parsed = parse_version(ver)
        if parsed is None or parsed[0] != major:
            continue
        if best is None or version_gt(ver, best):
            best = ver
    return best


def _download(url: str, dest: Path, *, timeout: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    if tmp.stat().st_size < 100_000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded Plotly bundle looks too small ({tmp})")
    tmp.replace(dest)


def check_and_update(*, timeout: float = 12.0, force: bool = False) -> dict[str, Any]:
    """If online, upgrade the local bundle within major_pin when a newer release exists.

    Offline or on any network error: keep the existing local bundle and report that.
    """
    try:
        manifest = ensure_local_bundle(timeout=timeout)
    except Exception as exc:
        return current_status() | {"ok": False, "error": str(exc)}

    try:
        latest = latest_version_for_major(manifest.major_pin, timeout=timeout)
    except Exception as exc:
        _set_status(
            version=manifest.version,
            checked=True,
            online=False,
            updated=False,
            message=f"offline or unreachable; keeping {manifest.version} ({exc})",
        )
        logger.info("Plotly update check skipped (offline): %s", exc)
        return current_status() | {"ok": True}

    if latest is None:
        _set_status(
            version=manifest.version,
            checked=True,
            online=True,
            updated=False,
            message=f"no npm versions found for major {manifest.major_pin}",
        )
        return current_status() | {"ok": True}

    if not force and not version_gt(latest, manifest.version):
        _set_status(
            version=manifest.version,
            checked=True,
            online=True,
            updated=False,
            message=f"up to date ({manifest.version})",
        )
        return current_status() | {"ok": True, "latest": latest}

    url = CDN_TEMPLATE.format(version=latest)
    logger.info("Updating Plotly.js %s → %s", manifest.version, latest)
    try:
        _download(url, BUNDLE_PATH, timeout=max(timeout, 30.0))
    except Exception as exc:
        _set_status(
            version=manifest.version,
            checked=True,
            online=True,
            updated=False,
            message=f"update to {latest} failed; keeping {manifest.version} ({exc})",
        )
        logger.warning("Plotly update failed; keeping %s: %s", manifest.version, exc)
        return current_status() | {"ok": True, "latest": latest, "error": str(exc)}

    updated = PlotlyManifest(version=latest, source=url, major_pin=manifest.major_pin)
    updated.dump()
    _set_status(
        version=latest,
        checked=True,
        online=True,
        updated=True,
        message=f"updated to {latest}",
    )
    return current_status() | {"ok": True, "latest": latest}


def start_background_update_check() -> None:
    """Fire-and-forget update check so serve stays fast offline."""

    def _run() -> None:
        try:
            check_and_update()
        except Exception as exc:  # noqa: BLE001
            logger.debug("background Plotly check failed: %s", exc)

    thread = threading.Thread(target=_run, name="sand-plotly-update", daemon=True)
    thread.start()
