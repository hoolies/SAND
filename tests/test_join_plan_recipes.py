"""JoinPlan recipes, cancel/timeout, rows peek, rename."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sand.api.app import app
from sand.core.config import Settings
from sand.core.dataset_meta import get_recipe, list_recipes, save_recipe
from sand.db.active_queries import interrupt, register, unregister
from sand.db.duckdb_client import DuckDBClient
from sand.queries.joins import JoinPlan, JoinSpec


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    import sand.api.routes.datasets as routes_ds
    import sand.core.config as config_mod
    import sand.core.limits as limits_mod
    import sand.core.store as store_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir, llm_api_key="", query_timeout_seconds=0.05)
    monkeypatch.setenv("SAND_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(limits_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_ds, "get_settings", lambda: settings)
    return TestClient(app)


def test_save_and_run_join_plan_recipe(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
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
                data={"dataset_id": "multi", "replace": "true"},
                files={"file": (f"{name}.csv", fh, "text/csv")},
            ).status_code == 200

    plan = {
        "steps": [
            {"left": "a", "right": "b", "on": ["id"], "how": "inner"},
            {"left": "__prev__", "right": "c", "on": ["id"], "how": "inner"},
        ],
        "as_table": "abc",
        "limit": 50,
    }
    save = client.post(
        "/query/join/recipes",
        json={"dataset_id": "multi", "name": "chain", "plan": plan},
    )
    assert save.status_code == 200, save.text
    assert save.json()["recipe"]["plan"] is not None
    assert save.json()["recipe"]["spec"] is None

    listed = client.get("/query/join/recipes/multi").json()["recipes"]
    assert any(r["name"] == "chain" and r.get("plan") for r in listed)

    run = client.post("/query/join", json={"dataset_id": "multi", "recipe_name": "chain"})
    assert run.status_code == 200, run.text
    assert run.json()["as_table"] == "abc"
    assert run.json()["row_count"] >= 1


def test_join_with_recipe_name_saves_plan(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    for name, df in {
        "a": pd.DataFrame({"id": [1], "x": [1]}),
        "b": pd.DataFrame({"id": [1], "y": [2]}),
        "c": pd.DataFrame({"id": [1], "z": [3]}),
    }.items():
        path = tmp_path / f"{name}.csv"
        df.to_csv(path, index=False)
        with path.open("rb") as fh:
            client.post(
                "/datasets/upload",
                data={"dataset_id": "p", "replace": "true"},
                files={"file": (f"{name}.csv", fh, "text/csv")},
            )
    res = client.post(
        "/query/join",
        json={
            "dataset_id": "p",
            "recipe_name": "plan_save",
            "plan": {
                "steps": [
                    {"left": "a", "right": "b", "on": ["id"], "how": "left"},
                    {"left": "__prev__", "right": "c", "on": ["id"], "how": "left"},
                ],
                "limit": 10,
            },
        },
    )
    assert res.status_code == 200, res.text
    recipes = client.get("/query/join/recipes/p").json()["recipes"]
    hit = next(r for r in recipes if r["name"] == "plan_save")
    assert hit["plan"] and len(hit["plan"]["steps"]) == 2


def test_rows_peek_and_rename(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csv_path = tmp_path / "sales.csv"
    pd.DataFrame({"region": ["E", "W", "N"], "amount": [1, 2, 3]}).to_csv(csv_path, index=False)
    with csv_path.open("rb") as fh:
        assert client.post("/datasets/upload", files={"file": ("sales.csv", fh, "text/csv")}).status_code == 200
    rows = client.get("/datasets/sales/rows/sales?limit=2&offset=0")
    assert rows.status_code == 200, rows.text
    body = rows.json()
    assert body["row_count"] == 2
    assert body["total_rows"] == 3
    renamed = client.post("/datasets/sales/rename", json={"new_id": "shop"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["dataset_id"] == "shop"
    assert client.get("/datasets/shop/schema").status_code == 200
    assert client.get("/datasets/sales/schema").status_code == 404


def test_query_timeout_raises(tmp_path: Path, monkeypatch) -> None:
    import sand.core.config as config_mod

    settings = Settings(data_dir=tmp_path / "data", query_timeout_seconds=0.05)
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    db = tmp_path / "t.duckdb"
    duck = DuckDBClient(db)
    try:
        qid = register(duck, "t", "timeout-q")
        assert interrupt(query_id=qid) == 1
        unregister(qid)
        with pytest.raises(TimeoutError, match="SAND_QUERY_TIMEOUT_SECONDS"):
            duck.execute("SELECT count(*) FROM range(200000000)")
    finally:
        duck.close()


def test_legacy_spec_recipe_still_loads(tmp_path: Path) -> None:
    db = tmp_path / "r.duckdb"
    duck = DuckDBClient(db)
    duck.execute("CREATE TABLE a AS SELECT 1 AS id")
    duck.execute("CREATE TABLE b AS SELECT 1 AS id")
    spec = JoinSpec(left="a", right="b", on=["id"], how="inner")
    save_recipe(duck, "legacy", spec=spec)
    recipes = list_recipes(duck)
    assert recipes[0].spec is not None
    assert recipes[0].plan is None
    got = get_recipe(duck, "legacy")
    assert got and got.spec and got.spec.left == "a"
    plan = JoinPlan(steps=[spec, JoinSpec(left="__prev__", right="b", on=["id"], how="left")])
    save_recipe(duck, "with_plan", plan=plan)
    got_plan = get_recipe(duck, "with_plan")
    assert got_plan and got_plan.plan and len(got_plan.plan.steps) == 2
    duck.close()
