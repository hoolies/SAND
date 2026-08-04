"""Guards, pool reuse, offline asks, and list-datasets shape."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sand.api.app import app
from sand.core.config import Settings
from sand.core.limits import ResourceLimitError, check_file_size, guard_result_rows, limits_from_settings
from sand.db.duckdb_client import DuckDBClient
from sand.db.pool import close_all, close_client, get_client
from sand.ingest.loader import ingest_files


@pytest.fixture(autouse=True)
def _close_pool():
    yield
    close_all()


def test_check_file_size_blocks(tmp_path: Path) -> None:
    p = tmp_path / "big.csv"
    p.write_bytes(b"x" * 100)
    with pytest.raises(ResourceLimitError):
        check_file_size(p, max_bytes=50, label="big.csv")


def test_guard_result_rows(tmp_path: Path) -> None:
    db = tmp_path / "g.duckdb"
    with DuckDBClient(db) as client:
        client.execute("CREATE TABLE t AS SELECT * FROM range(100)")
        with pytest.raises(ResourceLimitError):
            guard_result_rows(client, "SELECT * FROM t", max_rows=10, action="test")
        assert guard_result_rows(client, "SELECT * FROM t LIMIT 5", max_rows=10) == 5


def test_connection_reuse(tmp_path: Path) -> None:
    db = tmp_path / "pool.duckdb"
    a = get_client(db, read_only=False)
    b = get_client(db, read_only=False)
    assert a is b
    a.execute("CREATE TABLE IF NOT EXISTS x (id INTEGER)")
    close_client(db)


def test_join_no_pandas_fallback_and_ctas(tmp_path: Path) -> None:
    from sand.queries.joins import JoinSpec, execute_join

    left = tmp_path / "a.csv"
    right = tmp_path / "b.csv"
    pd.DataFrame({"id": [1, 2], "v": [10, 20]}).to_csv(left, index=False)
    pd.DataFrame({"id": [1], "name": ["x"]}).to_csv(right, index=False)
    db = tmp_path / "j.duckdb"
    ingest_files([left, right], dataset_id="j", db_path=db)
    client = get_client(db, read_only=False)
    df, sql = execute_join(
        client,
        JoinSpec(left="a", right="b", on=["id"], how="inner", as_table="joined"),
    )
    assert "JOIN" in sql.upper()
    assert "joined" in client.table_names()
    assert len(df) == 1


def test_list_datasets_shape_and_common_ask(tmp_path: Path, monkeypatch) -> None:
    import sand.api.routes.datasets as routes_ds
    import sand.core.config as config_mod
    import sand.core.limits as limits_mod
    import sand.core.store as store_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, max_result_rows=1000)
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_ds, "get_settings", lambda: settings)
    monkeypatch.setattr(limits_mod, "get_settings", lambda: settings)

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "legacy.db").write_bytes(b"sqlite")

    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["East", "West", "East"], "amount": [10, 20, 5]}).to_csv(csv_path, index=False)

    client = TestClient(app)
    empty = client.get("/datasets")
    assert empty.status_code == 200
    body = empty.json()
    assert body["empty"] is True
    assert "datasets" in body
    assert any(o["stem"] == "legacy" for o in body["orphans"])

    with csv_path.open("rb") as fh:
        up = client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")})
    assert up.status_code == 200, up.text

    listed = client.get("/datasets").json()
    assert listed["empty"] is False
    assert any(d["id"] == "sales" for d in listed["datasets"])

    ask = client.post(
        "/chat/common-ask",
        json={
            "dataset_id": "sales",
            "action": "groupby",
            "table": "sales",
            "params": {"group_by": ["region"], "metric": "amount"},
        },
    )
    assert ask.status_code == 200, ask.text
    assert ask.json()["row_count"] == 2

    top = client.post(
        "/chat/common-ask",
        json={"dataset_id": "sales", "action": "top_n", "table": "sales", "params": {"column": "amount", "n": 2}},
    )
    assert top.status_code == 200, top.text
    assert top.json()["row_count"] == 2


def test_limits_from_settings() -> None:
    lim = limits_from_settings(Settings(max_result_rows=123, max_export_rows=456))
    assert lim.max_result_rows == 123
    assert lim.max_export_rows == 456


def test_sanitize_dataset_id_and_sample_shape(tmp_path: Path, monkeypatch) -> None:
    import sand.core.config as config_mod
    import sand.samples as samples_mod
    from sand.core.config import Settings, sanitize_dataset_id
    from sand.samples import load_sample_shop

    assert sanitize_dataset_id("shop") == "shop"
    assert sanitize_dataset_id("My Shop!") == "My_Shop"
    with pytest.raises(ValueError):
        sanitize_dataset_id("../evil")
    with pytest.raises(ValueError):
        sanitize_dataset_id("..")
    with pytest.raises(ValueError):
        sanitize_dataset_id("a/b")

    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(ValueError):
        settings.db_path("../escape")
    path = settings.db_path("ok_id")
    assert path.parent == settings.data_dir.resolve()
    assert path.name == "ok_id.duckdb"

    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(samples_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(samples_mod, "SAMPLES_DIR", tmp_path / "samples")
    payload = load_sample_shop("shop")
    assert "source_files" in payload
    assert payload["tables"]
    assert {"name", "sheet_name", "row_count", "columns", "source_file"} <= set(payload["tables"][0])
    assert "suggested_joins" in payload


def test_parquet_and_xlsx_native(tmp_path: Path) -> None:
    import duckdb
    from openpyxl import Workbook

    from sand.db.pool import close_all, get_client
    from sand.ingest.loader import ingest_files

    close_all()
    pq = tmp_path / "facts.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT 1 AS id, 'East' AS region UNION ALL SELECT 2, 'West') TO '{pq}' (FORMAT PARQUET)"
    )

    xlsx = tmp_path / "sales.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["region", "amount"])
    ws.append(["East", 10])
    ws.append(["West", 20])
    wb.save(xlsx)

    db = tmp_path / "mix.duckdb"
    result = ingest_files([pq, xlsx], dataset_id="mix", db_path=db)
    assert {t.name for t in result.tables} >= {"facts", "sales"}
    client = get_client(db, read_only=False)
    assert int(client.fetchall("SELECT COUNT(*) FROM facts")[0][0]) == 2
    assert int(client.fetchall("SELECT COUNT(*) FROM sales")[0][0]) == 2
    client.checkpoint()
    from sand.db.pool import close_client

    close_client(db)
    assert len(db.read_bytes()) > 100


def test_orphan_delete_and_health(tmp_path: Path, monkeypatch) -> None:
    import sand.api.routes.datasets as routes_ds
    import sand.core.config as config_mod
    import sand.core.store as store_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, llm_api_key="")
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_ds, "get_settings", lambda: settings)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "old.db").write_bytes(b"sqlite")

    client = TestClient(app)
    listed = client.get("/datasets").json()
    assert any(o["stem"] == "old" for o in listed["orphans"])
    deleted = client.delete("/datasets/orphans/old")
    assert deleted.status_code == 200, deleted.text
    assert not (data_dir / "old.db").exists()

    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert "llm_configured" in health
    assert "llm_reachable" in health


def test_filter_rejects_raw_where_api(tmp_path: Path, monkeypatch) -> None:
    import sand.api.routes.datasets as routes_ds
    import sand.core.config as config_mod
    import sand.core.store as store_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir)
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_ds, "get_settings", lambda: settings)

    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["East", "West"], "amount": [10, 20]}).to_csv(csv_path, index=False)
    client = TestClient(app)
    with csv_path.open("rb") as fh:
        assert client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")}).status_code == 200

    bad = client.post(
        "/query/common",
        json={
            "dataset_id": "sales",
            "action": "filter",
            "table": "sales",
            "params": {"where": "region = 'East' OR 1=1"},
        },
    )
    assert bad.status_code == 400
    detail = bad.json()["detail"]
    msg = detail["message"] if isinstance(detail, dict) else str(detail)
    assert "where" in msg.lower() or "filters" in msg.lower()
    if isinstance(detail, dict):
        assert detail.get("code") == "bad_request"

    good = client.post(
        "/query/common",
        json={
            "dataset_id": "sales",
            "action": "filter",
            "table": "sales",
            "params": {"filters": [{"column": "region", "op": "eq", "value": "East"}]},
        },
    )
    assert good.status_code == 200, good.text
    assert good.json()["row_count"] == 1
    assert "columns" in good.json() and "rows" in good.json()

    gone = client.post(
        "/query/common",
        json={"dataset_id": "sales", "action": "join", "params": {"left": "sales", "right": "sales"}},
    )
    assert gone.status_code == 410
    assert gone.json()["detail"]["code"] == "deprecated"


def test_limit_literal_does_not_bypass_guard(tmp_path: Path) -> None:
    from sand.core.limits import estimate_sql_rows
    from sand.core.sql_scan import find_limit_value

    assert find_limit_value("SELECT * FROM t WHERE x = 'LIMIT 1'") is None
    assert find_limit_value("SELECT * FROM t LIMIT 5") == 5

    db = tmp_path / "lim.duckdb"
    with DuckDBClient(db) as client:
        client.execute("CREATE TABLE t AS SELECT * FROM range(50)")
        # String containing LIMIT must not be treated as a row cap
        n = estimate_sql_rows(client, "SELECT * FROM t WHERE CAST(range AS VARCHAR) != 'LIMIT 1'")
        assert n == 50


def test_api_token_middleware(tmp_path: Path, monkeypatch) -> None:
    import sand.api.app as app_mod
    import sand.core.config as config_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, api_token="secret-token")
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(app_mod, "get_settings", lambda: settings)

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    denied = client.get("/datasets")
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "unauthorized"
    ok = client.get("/datasets", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200
    ok2 = client.get("/datasets", headers={"X-SAND-Token": "secret-token"})
    assert ok2.status_code == 200


def test_data_dir_budget_and_bad_column(tmp_path: Path, monkeypatch) -> None:
    import sand.core.config as config_mod
    import sand.core.limits as limits_mod
    from sand.core.limits import check_data_dir_budget
    from sand.queries.common import CommonQueries

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "pad.bin").write_bytes(b"x" * 1000)
    settings = Settings(data_dir=data_dir, max_data_dir_bytes=1500)
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(limits_mod, "get_settings", lambda: settings)

    with pytest.raises(ResourceLimitError):
        check_data_dir_budget(additional_bytes=600, settings=settings)

    db = tmp_path / "cols.duckdb"
    with DuckDBClient(db) as client:
        client.execute("CREATE TABLE sales AS SELECT 'East' AS region, 10 AS amount")
        q = CommonQueries(client)
        with pytest.raises(ValueError, match="Unknown"):
            q.top_n("sales", column="nope", n=1)


def test_export_csv_streams(tmp_path: Path, monkeypatch) -> None:
    import sand.core.config as config_mod
    import sand.core.store as store_mod
    from sand.db.pool import close_all
    from sand.ingest.loader import ingest_file

    close_all()
    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir)
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)

    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["East", "West"], "amount": [10, 20]}).to_csv(csv_path, index=False)
    ingest_file(csv_path, dataset_id="sales", db_path=settings.db_path("sales"))

    client = TestClient(app)
    resp = client.post(
        "/export/csv",
        json={"dataset_id": "sales", "table": "sales", "format": "csv"},
    )
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers.get("content-type", "")
    text = resp.text
    assert "region" in text and "East" in text

    xlsx = client.post(
        "/export/xlsx",
        json={"dataset_id": "sales", "table": "sales", "format": "xlsx"},
    )
    assert xlsx.status_code == 200, xlsx.text
    assert "spreadsheetml" in xlsx.headers.get("content-type", "")
    assert xlsx.content[:2] == b"PK"  # zip/xlsx


def test_chat_sidecar_history(tmp_path: Path, monkeypatch) -> None:
    import sand.core.chat_store as chat_mod
    import sand.core.config as config_mod
    from sand.core.chat_store import ChatTurn, append_chat, chat_sidecar_path, clear_chat, list_chat
    from sand.db.pool import close_all
    from sand.ingest.loader import ingest_file
    from sand.llm.nlsql import NLSQLChat

    close_all()
    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir)
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(chat_mod, "get_settings", lambda: settings)

    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["East", "West"], "amount": [10, 20]}).to_csv(csv_path, index=False)
    ingest_file(csv_path, dataset_id="sales", db_path=settings.db_path("sales"))

    append_chat("sales", ChatTurn(role="user", content="hi"), settings=settings)
    append_chat("sales", ChatTurn(role="assistant", content="hello", sql="SELECT 1"), settings=settings)
    turns = list_chat("sales", limit=10, settings=settings)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert chat_sidecar_path("sales", settings).exists()

    client = TestClient(app)
    hist = client.get("/chat/sales/history")
    assert hist.status_code == 200, hist.text
    assert len(hist.json()["turns"]) == 2

    # Read-only SQL ask uses dataset_id sidecar for history (not DuckDB writes)
    close_all()
    with DuckDBClient(settings.db_path("sales"), read_only=True) as db:
        result = NLSQLChat(db, dataset_id="sales").ask(
            "noop",
            sql_override="SELECT region, amount FROM sales",
            persist=False,
        )
        assert result.row_count >= 1

    cleared = client.delete("/chat/sales/history")
    assert cleared.status_code == 200
    assert list_chat("sales", settings=settings) == []
    clear_chat("sales", settings=settings)
