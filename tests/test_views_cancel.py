"""Saved views, cancel registry, and chat payload fields."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from sand.api.app import app
from sand.core.config import Settings
from sand.db.active_queries import interrupt, register, unregister
from sand.db.duckdb_client import DuckDBClient
from sand.llm.nlsql import NLSQLChat


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    import sand.api.routes.datasets as routes_ds
    import sand.core.config as config_mod
    import sand.core.limits as limits_mod
    import sand.core.store as store_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, llm_api_key="", chart_sample_rows=50, max_result_rows=500)
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(limits_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_ds, "get_settings", lambda: settings)
    return TestClient(app)


def test_saved_view_cache_and_over_cap(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["E", "W"] * 40, "amount": list(range(80))}).to_csv(csv_path, index=False)
    with csv_path.open("rb") as fh:
        assert client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")}).status_code == 200

    sql = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region"
    saved = client.post(
        "/chat/views",
        json={
            "dataset_id": "sales",
            "name": "by_region",
            "sql": sql,
            "cache_enabled": True,
            "allow_over_cap": True,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["view"]["allow_over_cap"] is True

    run1 = client.post("/chat/views/run", json={"dataset_id": "sales", "name": "by_region"})
    assert run1.status_code == 200, run1.text
    body1 = run1.json()
    assert body1["from_cache"] is False
    assert body1["cache_updated"] is True
    assert body1["sql"] == sql
    assert body1["row_count"] == 2
    assert body1["chart_sample_rows"] >= 50

    run2 = client.post("/chat/views/run", json={"dataset_id": "sales", "name": "by_region"})
    assert run2.status_code == 200, run2.text
    assert run2.json()["from_cache"] is True

    refresh = client.post(
        "/chat/views/run",
        json={"dataset_id": "sales", "name": "by_region", "refresh_cache": True, "use_cache": False},
    )
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["from_cache"] is False

    listed = client.get("/chat/views/sales")
    assert listed.status_code == 200
    assert any(v["name"] == "by_region" and v["has_cache"] for v in listed.json()["views"])

    cleared = client.delete("/chat/views/sales/by_region/cache")
    assert cleared.status_code == 200
    deleted = client.delete("/chat/views/sales/by_region")
    assert deleted.status_code == 200


def test_run_full_reports_chart_cap(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": [f"r{i}" for i in range(100)], "amount": list(range(100))}).to_csv(
        csv_path, index=False
    )
    with csv_path.open("rb") as fh:
        assert client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")}).status_code == 200

    sql = "SELECT region, amount FROM sales"
    res = client.post(
        "/chat",
        json={"dataset_id": "sales", "message": "run full", "run_full": True, "sql": sql},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_preview"] is False
    assert body["chart_sample_rows"] == 50
    assert body["row_count"] <= 50
    assert body["chart_capped"] is True


def test_cancel_interrupt_registry(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    duck = DuckDBClient(db)
    duck.execute("CREATE TABLE t AS SELECT 1 AS x")
    qid = register(duck, "t", "qid-1")
    assert qid == "qid-1"
    assert interrupt(query_id="qid-1") == 1
    unregister(qid)
    assert interrupt(dataset_id="t") == 0
    duck.close()


def test_bypass_chart_cap_uses_max_result_rows(tmp_path: Path, monkeypatch) -> None:
    import sand.core.config as config_mod
    import sand.core.limits as limits_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, llm_api_key="", chart_sample_rows=5, max_result_rows=80)
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(limits_mod, "get_settings", lambda: settings)

    db = tmp_path / "sales.duckdb"
    duck = DuckDBClient(db)
    duck.execute("CREATE TABLE sales AS SELECT i AS id, i AS amount FROM range(60) t(i)")
    chat = NLSQLChat(duck, dataset_id="sales")
    result = chat.ask(
        "full",
        run_full=True,
        sql_override="SELECT id, amount FROM sales",
        persist=False,
        bypass_chart_cap=True,
    )
    assert result.row_count == 60
    assert result.chart_sample_rows == 80
    duck.close()


def test_static_ui_assets(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    for path in ("/", "/static/styles.css", "/static/js/main.js", "/static/js/chat.js", "/static/js/sql.js"):
        res = client.get(path)
        assert res.status_code == 200, path
    html = client.get("/").text
    assert "theme-toggle" in html
    assert "cancel-query-btn" in html
    assert "view-over-cap" in html
    assert "view-sql" in html
    assert "sql-run-preview-btn" in html
    assert "catppuccin" in client.get("/static/js/main.js").text.lower() or "Catppuccin" in client.get(
        "/static/js/main.js"
    ).text
    assert "theme-toggle" in html
    assert "cancel-query-btn" in html
    assert "view-over-cap" in html
    assert "/static/vendor/plotly/plotly.min.js" in html
    assert "save-recipe-btn" not in html
    css = client.get("/static/styles.css").text
    assert "tokyo-night-storm" in css
    assert "catppuccin-latte" in css
