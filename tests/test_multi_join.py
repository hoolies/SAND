"""Tests for multi-spreadsheet ingest and explicit joins."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sand.api.app import app
from sand.core.config import Settings
from sand.db.duckdb_client import DuckDBClient
from sand.ingest.loader import ingest_files
from sand.queries.joins import JoinKey, JoinSpec, execute_join


@pytest.fixture
def two_csvs(tmp_path: Path) -> tuple[Path, Path]:
    sales = tmp_path / "sales.csv"
    customers = tmp_path / "customers.csv"
    pd.DataFrame(
        {
            "order_id": [1, 2, 3],
            "cust_id": [10, 20, 10],
            "amount": [100, 200, 50],
        }
    ).to_csv(sales, index=False)
    pd.DataFrame(
        {
            "id": [10, 20, 30],
            "name": ["Ada", "Bob", "Cara"],
            "region": ["East", "West", "East"],
        }
    ).to_csv(customers, index=False)
    return sales, customers


def test_ingest_multiple_files(two_csvs: tuple[Path, Path], tmp_path: Path) -> None:
    sales, customers = two_csvs
    db_path = tmp_path / "shop.duckdb"
    result = ingest_files([sales, customers], dataset_id="shop", db_path=db_path)
    assert result.dataset_id == "shop"
    assert {t.name for t in result.tables} == {"sales", "customers"}
    assert len(result.source_files) == 2

    with DuckDBClient(db_path) as client:
        assert set(client.table_names()) == {"sales", "customers"}


def test_join_with_renamed_keys(two_csvs: tuple[Path, Path], tmp_path: Path) -> None:
    sales, customers = two_csvs
    db_path = tmp_path / "shop.duckdb"
    ingest_files([sales, customers], dataset_id="shop", db_path=db_path)

    with DuckDBClient(db_path) as client:
        spec = JoinSpec(
            left="sales",
            right="customers",
            on=["cust_id=id"],
            how="left",
            as_table="sales_enriched",
        )
        df, sql = execute_join(client, spec)
        assert len(df) == 3
        assert "name" in df.columns
        assert "sales_enriched" in client.table_names()

        spec2 = JoinSpec(
            left="sales",
            right="customers",
            on=[JoinKey(left="cust_id", right="id")],
            how="inner",
        )
        df2, _ = execute_join(client, spec2)
        assert len(df2) == 3


def test_join_rejects_bad_keys(two_csvs: tuple[Path, Path], tmp_path: Path) -> None:
    sales, customers = two_csvs
    db_path = tmp_path / "shop.duckdb"
    ingest_files([sales, customers], dataset_id="shop", db_path=db_path)
    with DuckDBClient(db_path) as client:
        with pytest.raises(ValueError, match="not in left"):
            execute_join(
                client,
                JoinSpec(left="sales", right="customers", on=["missing=id"]),
            )


def test_api_multi_upload_and_join(two_csvs: tuple[Path, Path], tmp_path: Path, monkeypatch) -> None:
    sales, customers = two_csvs
    data_dir = tmp_path / "data"
    import sand.core.config as config_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: Settings(data_dir=data_dir))

    client = TestClient(app)
    with sales.open("rb") as s, customers.open("rb") as c:
        res = client.post(
            "/datasets/upload",
            data={"dataset_id": "shop"},
            files=[
                ("files", ("sales.csv", s, "text/csv")),
                ("files", ("customers.csv", c, "text/csv")),
            ],
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dataset_id"] == "shop"
    assert {t["name"] for t in body["tables"]} == {"sales", "customers"}

    join = client.post(
        "/query/join",
        json={
            "dataset_id": "shop",
            "join": {
                "left": "sales",
                "right": "customers",
                "on": ["cust_id=id"],
                "how": "inner",
                "as_table": "enriched",
            },
        },
    )
    assert join.status_code == 200, join.text
    assert join.json()["row_count"] == 3
    assert join.json()["as_table"] == "enriched"

    schema = client.get("/datasets/shop/schema")
    assert "enriched" in schema.json()["tables"]
