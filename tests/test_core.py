"""Smoke tests for ingest, common queries, SQL guardrails, and chart planning."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sand.charts.planner import plan_chart
from sand.db.duckdb_client import DuckDBClient
from sand.ingest.loader import ingest_file
from sand.llm.nlsql import assert_readonly_sql, with_eval_limit
from sand.queries.common import CommonQueries


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    df = pd.DataFrame(
        {
            "region": ["East", "West", "East", "North", "West", "East"],
            "amount": [100, 150, 80, 200, 120, 90],
            "order_date": [
                "2024-01-05",
                "2024-01-12",
                "2024-02-03",
                "2024-02-18",
                "2024-03-01",
                "2024-03-15",
            ],
        }
    )
    df.to_csv(path, index=False)
    return path


def test_ingest_round_trip(sample_csv: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "sales.duckdb"
    result = ingest_file(sample_csv, dataset_id="sales", db_path=db_path)
    assert result.dataset_id == "sales"
    assert len(result.tables) == 1
    assert result.tables[0].row_count == 6

    with DuckDBClient(db_path) as client:
        assert "sales" in client.table_names()
        df = client.to_dataframe("SELECT SUM(amount) AS total FROM sales")
        assert int(df.iloc[0]["total"]) == 740


def test_common_queries(sample_csv: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "sales.duckdb"
    ingest_file(sample_csv, dataset_id="sales", db_path=db_path)
    with DuckDBClient(db_path) as client:
        q = CommonQueries(client)
        profile = q.profile("sales")
        assert set(profile["column"]) >= {"region", "amount", "order_date"}

        grouped = q.groupby("sales", group_by=["region"], metric="amount", agg="sum")
        assert "sum_amount" in grouped.columns
        assert len(grouped) == 3

        top = q.top_n("sales", column="amount", n=2)
        assert len(top) == 2
        assert top.iloc[0]["amount"] == 200

        ts = q.time_series("sales", date_column="order_date", metric="amount", bucket="month")
        assert "period" in ts.columns
        assert len(ts) == 3

        filtered = q.filter_rows(
            "sales",
            filters=[
                {"column": "region", "op": "eq", "value": "East"},
                {"column": "amount", "op": "gte", "value": 90},
            ],
            order_by="amount",
            ascending=False,
        )
        assert len(filtered) == 2
        assert set(filtered["region"]) == {"East"}

        with pytest.raises(ValueError, match="Raw SQL"):
            q.filter_rows("sales", where="region = 'East'")


def test_sql_guardrails() -> None:
    assert assert_readonly_sql("SELECT * FROM sales") == "SELECT * FROM sales"
    assert assert_readonly_sql('SELECT * FROM "sales"')
    assert assert_readonly_sql("  WITH t AS (SELECT 1) SELECT * FROM t  ")
    assert assert_readonly_sql("SELECT replace(region, 'E', 'X') FROM sales")
    assert assert_readonly_sql("SELECT * FROM sales WHERE region = 'DROP'")

    with pytest.raises(ValueError):
        assert_readonly_sql("DELETE FROM sales")
    with pytest.raises(ValueError):
        assert_readonly_sql("SELECT 1; DROP TABLE sales")
    with pytest.raises(ValueError):
        assert_readonly_sql("UPDATE sales SET amount=0")
    with pytest.raises(ValueError):
        assert_readonly_sql("SELECT * FROM read_csv_auto('x.csv')")
    with pytest.raises(ValueError):
        assert_readonly_sql("SELECT * FROM '/tmp/x.csv'")
    with pytest.raises(ValueError):
        assert_readonly_sql("COPY sales TO 'out.csv'")
    with pytest.raises(ValueError):
        assert_readonly_sql("SELECT * FROM sales --\nDROP TABLE sales")
    # comment cannot hide a mid-statement side effect that still starts with SELECT
    with pytest.raises(ValueError):
        assert_readonly_sql("SELECT * FROM sales WHERE 1=1; COPY sales TO 'x'")
    with pytest.raises(ValueError):
        assert_readonly_sql("/* COPY */ SELECT * FROM read_parquet('x.parquet')")
    # LIMIT inside a string must not satisfy "has LIMIT" for guards; wrap still works
    assert with_eval_limit("SELECT * FROM sales WHERE note = 'LIMIT 999'") == (
        "SELECT * FROM (SELECT * FROM sales WHERE note = 'LIMIT 999') AS _sand_eval LIMIT 10"
    )


def test_chart_planner_heuristics() -> None:
    line_df = pd.DataFrame({"order_date": pd.to_datetime(["2024-01-01", "2024-02-01"]), "amount": [10, 20]})
    spec = plan_chart(line_df)
    assert spec.chart_type == "line"

    bar_df = pd.DataFrame({"region": ["A", "B", "C"], "amount": [1, 2, 3]})
    spec = plan_chart(bar_df)
    assert spec.chart_type == "bar"

    scatter_df = pd.DataFrame({"x": [1, 2, 3], "y": [3, 2, 1]})
    spec = plan_chart(scatter_df)
    assert spec.chart_type == "scatter"

    pie_df = pd.DataFrame({"region": ["A", "B"], "share": [0.4, 0.6]})
    spec = plan_chart(pie_df)
    assert spec.chart_type == "pie"

    preferred = plan_chart(bar_df, preferred="table")
    assert preferred.chart_type == "table"
