"""Plotly vendor helpers and recipe list UI contracts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from sand.api.app import app
from sand.web import plotly_vendor as pv


def test_version_compare() -> None:
    assert pv.version_gt("2.35.3", "2.35.2")
    assert not pv.version_gt("2.35.2", "2.35.3")
    assert not pv.version_gt("3.0.0", "not-a-version")


def test_check_and_update_keeps_local_when_offline(tmp_path: Path, monkeypatch) -> None:
    vendor = tmp_path / "vendor"
    bundle = vendor / "plotly.min.js"
    manifest = vendor / "manifest.json"
    vendor.mkdir()
    bundle.write_bytes(b"x" * 120_000)
    manifest.write_text(
        '{"version":"2.35.2","source":"https://cdn.plot.ly/plotly-2.35.2.min.js","major_pin":2}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pv, "VENDOR_DIR", vendor)
    monkeypatch.setattr(pv, "BUNDLE_PATH", bundle)
    monkeypatch.setattr(pv, "MANIFEST_PATH", manifest)

    with patch.object(pv, "latest_version_for_major", side_effect=httpx.ConnectError("offline")):
        status = pv.check_and_update()
    assert status["ok"] is True
    assert status["version"] == "2.35.2"
    assert status["online"] is False
    assert bundle.exists()


def test_check_and_update_downloads_newer(tmp_path: Path, monkeypatch) -> None:
    vendor = tmp_path / "vendor"
    bundle = vendor / "plotly.min.js"
    manifest = vendor / "manifest.json"
    vendor.mkdir()
    bundle.write_bytes(b"old" * 40_000)
    manifest.write_text(
        '{"version":"2.35.2","source":"https://cdn.plot.ly/plotly-2.35.2.min.js","major_pin":2}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pv, "VENDOR_DIR", vendor)
    monkeypatch.setattr(pv, "BUNDLE_PATH", bundle)
    monkeypatch.setattr(pv, "MANIFEST_PATH", manifest)

    def fake_download(url: str, dest: Path, *, timeout: float) -> None:
        dest.write_bytes(b"new" * 40_000)

    with (
        patch.object(pv, "latest_version_for_major", return_value="2.35.3"),
        patch.object(pv, "_download", side_effect=fake_download),
    ):
        status = pv.check_and_update()
    assert status["ok"] is True
    assert status["version"] == "2.35.3"
    assert status["updated"] is True
    assert "2.35.3" in manifest.read_text(encoding="utf-8")


def test_static_plotly_and_recipe_list_readonly() -> None:
    client = TestClient(app)
    assert pv.bundle_ready()
    html = client.get("/").text
    assert "/static/vendor/plotly/plotly.min.js" in html
    assert "cdn.plot.ly" not in html
    assert "save-recipe-btn" not in html
    assert "Run or delete saved recipes" in html or "Saved recipes" in html

    js = client.get("/static/js/join.js").text
    assert "delete-recipe" not in js
    assert 'data-act="del"' in js
    assert "recipe_name" in js

    bundle = client.get("/static/vendor/plotly/plotly.min.js")
    assert bundle.status_code == 200
    assert len(bundle.content) > 100_000
    assert b"plotly.js" in bundle.content[:200]

    health = client.get("/health").json()
    assert health["plotly"]["bundle_ready"] is True
    assert health["plotly"]["version"]
