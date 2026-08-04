"""0.9.3 polish: docs auth, plan estimate, write flag, nested plan SQL."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from sand.api.app import app
from sand.core.config import Settings
from sand.db.duckdb_client import DuckDBClient
from sand.queries.joins import JoinPlan, JoinSpec, build_nested_join_plan_sql, execute_join_plan


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    import sand.api.app as app_mod
    import sand.api.routes.datasets as routes_ds
    import sand.core.config as config_mod
    import sand.core.limits as limits_mod
    import sand.core.store as store_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, llm_api_key="", api_token="secret-token")
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SAND_API_TOKEN", "secret-token")
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(app_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(limits_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_ds, "get_settings", lambda: settings)
    return TestClient(app)


def test_docs_openapi_without_token(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/datasets").status_code == 401
    ok = client.get("/datasets", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200


def test_plan_estimate_and_nested_sql(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer secret-token"}
    for name, df in {
        "a": pd.DataFrame({"id": [1, 2], "x": [10, 20]}),
        "b": pd.DataFrame({"id": [1, 2], "y": ["p", "q"]}),
        "c": pd.DataFrame({"id": [1, 2], "z": [100, 200]}),
    }.items():
        path = tmp_path / f"{name}.csv"
        df.to_csv(path, index=False)
        with path.open("rb") as fh:
            assert client.post(
                "/datasets/upload",
                data={"dataset_id": "m", "replace": "true"},
                files={"file": (f"{name}.csv", fh, "text/csv")},
                headers=headers,
            ).status_code == 200

    est = client.post(
        "/query/join/estimate",
        json={
            "dataset_id": "m",
            "plan": {
                "steps": [
                    {"left": "a", "right": "b", "on": ["id"], "how": "inner"},
                    {"left": "__prev__", "right": "c", "on": ["id"], "how": "inner"},
                ]
            },
        },
        headers=headers,
    )
    assert est.status_code == 200, est.text
    body = est.json()
    assert body["plan"] is True
    assert body["estimate"]["final_estimated_rows"] is not None
    assert len(body["estimate"]["steps"]) == 2

    preview = client.post(
        "/query/join",
        json={
            "dataset_id": "m",
            "write": False,
            "plan": {
                "steps": [
                    {"left": "a", "right": "b", "on": ["id"], "how": "inner"},
                    {"left": "__prev__", "right": "c", "on": ["id"], "how": "inner"},
                ],
                "limit": 10,
            },
        },
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert "JOIN" in preview.json()["sql"].upper()
    assert "tmp_join_" not in preview.json()["sql"]

    bad = client.post(
        "/query/join",
        json={
            "dataset_id": "m",
            "write": False,
            "join": {"left": "a", "right": "b", "on": ["id"], "how": "inner", "as_table": "nope"},
        },
        headers=headers,
    )
    assert bad.status_code == 400


def test_nested_plan_unit(tmp_path: Path) -> None:
    db = tmp_path / "n.duckdb"
    duck = DuckDBClient(db)
    duck.execute("CREATE TABLE a AS SELECT 1 AS id, 10 AS x")
    duck.execute("CREATE TABLE b AS SELECT 1 AS id, 20 AS y")
    duck.execute("CREATE TABLE c AS SELECT 1 AS id, 30 AS z")
    plan = JoinPlan(
        steps=[
            JoinSpec(left="a", right="b", on=["id"], how="inner"),
            JoinSpec(left="__prev__", right="c", on=["id"], how="inner"),
        ],
        limit=5,
    )
    sql = build_nested_join_plan_sql(duck, plan)
    assert "SELECT" in sql.upper()
    df, out_sql = execute_join_plan(duck, plan)
    assert len(df) == 1
    assert "z" in df.columns
    assert out_sql == sql
    duck.close()


def test_ui_093_markers() -> None:
    client = TestClient(app)
    html = client.get("/").text
    assert "upload-cancel-btn" in html
    assert "sql-insert-table" in html
    assert "load-recipe" in client.get("/static/js/join.js").text
