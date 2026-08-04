"""Join cancel tracking, RO preview, temp cleanup, token compare."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sand.api.app import _api_token_ok, app
from sand.core.config import Settings
from sand.db.duckdb_client import DuckDBClient
from sand.queries.joins import JoinPlan, JoinSpec, execute_join_plan


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


def _upload_abc(client: TestClient, tmp_path: Path, dataset_id: str = "multi") -> None:
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
                data={"dataset_id": dataset_id, "replace": "true"},
                files={"file": (f"{name}.csv", fh, "text/csv")},
            ).status_code == 200


def test_join_preview_and_plan_without_materialize(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _upload_abc(client, tmp_path)

    preview = client.post(
        "/query/join",
        json={
            "dataset_id": "multi",
            "join": {"left": "a", "right": "b", "on": ["id"], "how": "inner", "limit": 10},
        },
        headers={"X-SAND-Query-Id": "join-preview-1"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json().get("as_table") in (None, "")
    tables = client.get("/datasets/multi/schema").json()["tables"]
    assert set(tables) >= {"a", "b", "c"}
    assert "joined" not in tables
    plan = client.post(
        "/query/join",
        json={
            "dataset_id": "multi",
            "plan": {
                "steps": [
                    {"left": "a", "right": "b", "on": ["id"], "how": "inner"},
                    {"left": "__prev__", "right": "c", "on": ["id"], "how": "inner"},
                ],
                "limit": 10,
            },
        },
        headers={"X-SAND-Query-Id": "join-plan-1"},
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["row_count"] >= 1
    tables = client.get("/datasets/multi/schema").json()["tables"]
    assert "tmp_join_0" not in tables
    assert "tmp_join_1" not in tables


def test_join_plan_temp_cleanup_on_failure(tmp_path: Path) -> None:
    """Nested SQL plans must not leave tmp_join_* tables after a failed step."""
    db = tmp_path / "t.duckdb"
    duck = DuckDBClient(db)
    duck.execute("CREATE TABLE a AS SELECT 1 AS id, 10 AS x")
    duck.execute("CREATE TABLE b AS SELECT 1 AS id, 20 AS y")
    duck.execute("CREATE TABLE c AS SELECT 1 AS id, 30 AS z")
    plan = JoinPlan(
        steps=[
            JoinSpec(left="a", right="b", on=["id"], how="inner"),
            JoinSpec(left="__prev__", right="c", on=["missing"], how="inner"),
        ]
    )
    with pytest.raises(ValueError, match="not in"):
        execute_join_plan(duck, plan)
    leftover = duck.fetchall(
        "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'tmp_join_%'"
    )
    assert leftover == []
    duck.close()


def test_recipe_run_does_not_require_resave(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _upload_abc(client, tmp_path, "p")
    save = client.post(
        "/query/join/recipes",
        json={
            "dataset_id": "p",
            "name": "ab",
            "join": {"left": "a", "right": "b", "on": ["id"], "how": "left", "limit": 5},
        },
    )
    assert save.status_code == 200, save.text
    run = client.post("/query/join", json={"dataset_id": "p", "recipe_name": "ab"})
    assert run.status_code == 200, run.text
    assert run.json()["row_count"] >= 1


def test_api_token_compare_digest() -> None:
    assert _api_token_ok("secret-token", "secret-token")
    assert not _api_token_ok("secret-tokex", "secret-token")
    assert not _api_token_ok("", "secret-token")
    assert not _api_token_ok("short", "secret-token")


def test_ui_has_join_cancel() -> None:
    client = TestClient(app)
    html = client.get("/").text
    assert "join-cancel-btn" in html
    js = client.get("/static/js/join.js").text
    assert "X-SAND-Query-Id" in client.get("/static/js/api.js").text or "queryId" in js
    assert "cancelActiveJoin" in js or "joinCancelBtn" in js
