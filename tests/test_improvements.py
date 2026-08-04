"""Tests for join suggestions, eval LIMIT, samples, recipes, types."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sand.api.app import app
from sand.core.config import Settings
from sand.core.dataset_meta import list_recipes, save_recipe
from sand.db.duckdb_client import DuckDBClient
from sand.ingest.loader import ingest_files
from sand.ingest.typing import build_type_plan
from sand.llm.nlsql import assert_readonly_sql, with_eval_limit
from sand.queries.join_suggest import estimate_join, suggest_join_keys
from sand.queries.joins import JoinSpec
from sand.samples import load_sample_shop


@pytest.fixture
def shop_db(tmp_path: Path) -> Path:
    sales = tmp_path / "sales.csv"
    customers = tmp_path / "customers.csv"
    pd.DataFrame({"order_id": [1, 2, 3], "cust_id": [10, 20, 10], "amount": [1, 2, 3]}).to_csv(sales, index=False)
    pd.DataFrame({"id": [10, 20], "name": ["A", "B"]}).to_csv(customers, index=False)
    db = tmp_path / "shop.duckdb"
    ingest_files([sales, customers], dataset_id="shop", db_path=db)
    return db


def test_with_eval_limit() -> None:
    assert with_eval_limit("SELECT * FROM sales") == (
        "SELECT * FROM (SELECT * FROM sales) AS _sand_eval LIMIT 10"
    )
    assert with_eval_limit("SELECT * FROM sales LIMIT 200") == (
        "SELECT * FROM (SELECT * FROM sales LIMIT 200) AS _sand_eval LIMIT 10"
    )
    assert assert_readonly_sql("SELECT 1") == "SELECT 1"
    assert assert_readonly_sql("SELECT * FROM sales", allowed_tables=["sales"]) == "SELECT * FROM sales"
    with pytest.raises(ValueError, match="disallowed|Unknown|Schema"):
        assert_readonly_sql("SELECT * FROM information_schema.tables", allowed_tables=["sales"])
    with pytest.raises(ValueError, match="disallowed|Unknown"):
        assert_readonly_sql("SELECT * FROM other", allowed_tables=["sales"])


def test_suggest_and_estimate(shop_db: Path) -> None:
    with DuckDBClient(shop_db) as client:
        suggestions = suggest_join_keys(client, "sales", "customers")
        assert suggestions
        assert any(s.left == "cust_id" and s.right == "id" for s in suggestions)

        spec = JoinSpec(left="sales", right="customers", on=["cust_id=id"], how="inner")
        est = estimate_join(client, spec)
        assert est.left_rows == 3
        assert est.right_rows == 2
        assert est.estimated_rows == 3
        assert est.multiplicity in {"many_to_one", "one_to_many", "one_to_one", "many_to_many"}


def test_recipes(shop_db: Path) -> None:
    with DuckDBClient(shop_db) as client:
        spec = JoinSpec(left="sales", right="customers", on=["cust_id=id"])
        save_recipe(client, "sales_customers", spec)
        recipes = list_recipes(client)
        assert any(r.name == "sales_customers" for r in recipes)


def test_type_plan(shop_db: Path) -> None:
    with DuckDBClient(shop_db) as client:
        df = client.to_dataframe("SELECT * FROM sales")
        plan = build_type_plan("sales", df)
        by_name = {c.name: c.inferred for c in plan.columns}
        assert by_name["amount"] in {"integer", "float"}
        typed = plan.apply(df)
        assert len(typed) == len(df)


def test_sample_and_e2e_api(tmp_path: Path, monkeypatch) -> None:
    import sand.core.config as config_mod
    import sand.core.store as store_mod
    import sand.samples as samples_mod

    data_dir = tmp_path / "data"
    settings = Settings(data_dir=data_dir)
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(store_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(samples_mod, "SAMPLES_DIR", tmp_path / "samples")
    monkeypatch.setattr(samples_mod, "get_settings", lambda: settings)

    result = load_sample_shop("shop")
    assert result["dataset_id"] == "shop"
    assert "source_files" in result
    assert {t["name"] for t in result["tables"]} >= {"sales", "customers", "products"}
    assert "sheet_name" in result["tables"][0]
    assert result["suggested_joins"]

    client = TestClient(app)
    # reload settings for API via same monkeypatch
    sample = client.post("/datasets/samples/shop?dataset_id=shop2")
    assert sample.status_code == 200, sample.text

    suggest = client.post(
        "/query/join/suggest",
        json={"dataset_id": "shop2", "left": "sales", "right": "customers"},
    )
    assert suggest.status_code == 200, suggest.text
    assert suggest.json()["suggestions"]

    est = client.post(
        "/query/join/estimate",
        json={
            "dataset_id": "shop2",
            "join": {"left": "sales", "right": "customers", "on": ["cust_id=id"], "how": "inner"},
        },
    )
    assert est.status_code == 200, est.text
    assert est.json()["estimate"]["estimated_rows"] is not None

    joined = client.post(
        "/query/join",
        json={
            "dataset_id": "shop2",
            "join": {
                "left": "sales",
                "right": "customers",
                "on": [{"left": "cust_id", "right": "id"}],
                "how": "left",
                "as_table": "enriched",
            },
            "recipe_name": "sales_to_customers",
        },
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["row_count"] == 6

    recipes = client.get("/query/join/recipes/shop2")
    assert recipes.status_code == 200
    assert any(r["name"] == "sales_to_customers" for r in recipes.json()["recipes"])

    profile = client.get("/datasets/shop2/profile/sales")
    assert profile.status_code == 200
    assert profile.json()["profile"]

    types = client.get("/datasets/shop2/types/sales")
    assert types.status_code == 200
    assert types.json()["plan"]["columns"]

    export_db = client.post("/export/db", json={"dataset_id": "shop2"})
    assert export_db.status_code == 200
    assert len(export_db.content) > 100

    export_csv = client.post("/export/csv", json={"dataset_id": "shop2", "table": "sales"})
    assert export_csv.status_code == 200
    assert b"order_id" in export_csv.content

    # hygiene
    renamed = client.post("/datasets/shop2/tables/products/rename", json={"new_name": "items"})
    assert renamed.status_code == 200, renamed.text
    dropped = client.delete("/datasets/shop2/tables/items")
    assert dropped.status_code == 200

    dup = client.post("/datasets/shop2/duplicate?new_id=shop2_copy")
    assert dup.status_code == 200, dup.text

    web = client.get("/")
    assert web.status_code == 200
    assert b"Join" in web.content
