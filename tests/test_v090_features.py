"""New 0.9.0 API surfaces: SQL, sheets, import, filter, parquet, lineage, limits."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient
from openpyxl import Workbook

from sand.api.app import app
from sand.core.config import Settings
from sand.db.duckdb_client import DuckDBClient


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    import sand.api.routes.datasets as routes_ds
    import sand.core.config as config_mod
    import sand.core.limits as limits_mod
    import sand.core.store as store_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, llm_api_key="")
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(limits_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_ds, "get_settings", lambda: settings)
    return TestClient(app)


def test_health_includes_limits(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    health = client.get("/health").json()
    assert "limits" in health
    assert health["limits"]["chart_sample_rows"] > 0
    assert health["version"]


def test_sql_endpoint_no_llm(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["E", "W"], "amount": [1, 2]}).to_csv(csv_path, index=False)
    with csv_path.open("rb") as fh:
        assert client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")}).status_code == 200
    preview = client.post(
        "/query/sql",
        json={"dataset_id": "sales", "sql": "SELECT region, amount FROM sales"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["is_preview"] is True
    assert preview.json()["row_count"] <= 10
    full = client.post(
        "/query/sql",
        json={"dataset_id": "sales", "sql": "SELECT region, amount FROM sales", "run_full": True},
    )
    assert full.status_code == 200, full.text
    assert full.json()["is_preview"] is False


def test_filter_common_ask(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["East", "West"], "amount": [10, 20]}).to_csv(csv_path, index=False)
    with csv_path.open("rb") as fh:
        assert client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")}).status_code == 200
    res = client.post(
        "/chat/common-ask",
        json={
            "dataset_id": "sales",
            "action": "filter",
            "table": "sales",
            "params": {"filters": [{"column": "region", "op": "eq", "value": "East"}]},
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["row_count"] == 1


def test_xlsx_sheets_and_filtered_ingest(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    xlsx = tmp_path / "book.xlsx"
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Keep"
    ws1.append(["a", "b"])
    ws1.append([1, 2])
    ws2 = wb.create_sheet("Skip")
    ws2.append(["x"])
    ws2.append([9])
    wb.save(xlsx)

    with xlsx.open("rb") as fh:
        listed = client.post("/datasets/xlsx/sheets", files={"file": ("book.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert listed.status_code == 200, listed.text
    assert set(listed.json()["sheets"]) >= {"Keep", "Skip"}

    with xlsx.open("rb") as fh:
        up = client.post(
            "/datasets/upload",
            data={"dataset_id": "book", "replace": "true", "sheets": '["Keep"]'},
            files={"file": ("book.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert up.status_code == 200, up.text
    tables = {t["name"] for t in up.json()["tables"]}
    # Single selected sheet uses the file stem as the table name
    assert tables == {"book"} or any("keep" in t for t in tables)
    assert not any("skip" in t for t in tables)
    schema = client.get("/datasets/book/schema").json()
    assert "Skip" not in { (schema.get("lineage") or {}).get(t, {}).get("sheet_name") for t in schema["tables"] }
    assert any(
        (schema.get("lineage") or {}).get(t, {}).get("sheet_name") == "Keep"
        for t in schema["tables"]
    )


def test_schema_lineage_and_parquet_export(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["E"], "amount": [1]}).to_csv(csv_path, index=False)
    with csv_path.open("rb") as fh:
        assert client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")}).status_code == 200
    schema = client.get("/datasets/sales/schema").json()
    assert "lineage" in schema
    assert "sales" in schema["lineage"]
    pq = client.post("/export/parquet", json={"dataset_id": "sales", "table": "sales", "format": "parquet"})
    assert pq.status_code == 200, pq.text
    assert len(pq.content) > 50


def test_import_duckdb(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    src = tmp_path / "external.duckdb"
    duck = DuckDBClient(src)
    duck.execute("CREATE TABLE t AS SELECT 1 AS x")
    duck.close()
    with src.open("rb") as fh:
        res = client.post(
            "/datasets/import",
            data={"dataset_id": "imported"},
            files={"file": ("external.duckdb", fh, "application/octet-stream")},
        )
    assert res.status_code == 200, res.text
    assert res.json()["dataset_id"] == "imported"
    assert "t" in res.json()["tables"]


def test_ui_has_sql_tab_and_sheet_picker() -> None:
    client = TestClient(app)
    html = client.get("/").text
    assert 'data-view="sql"' in html
    assert "view-sql" in html
    assert "sheet-picker" in html
    assert "import-duckdb" in html
    assert "upload-dropzone" in html
    assert 'href="/docs"' in html
    assert "rename-dataset-btn" in html
    assert client.get("/static/js/sql.js").status_code == 200
