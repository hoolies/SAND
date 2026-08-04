"""Jupyter helpers wrapping the shared SAND core."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from sand.charts.planner import plan_chart
from sand.charts.plotly_renderer import render_figure
from sand.charts.specs import ChartSpec, ChartType
from sand.core.config import get_settings
from sand.db.duckdb_client import DuckDBClient
from sand.ingest.loader import ingest_file, ingest_files
from sand.llm.nlsql import NLSQLChat
from sand.queries.common import CommonQueries
from sand.queries.joins import JoinKey, JoinPlan, JoinSpec, execute_join, execute_join_plan
from sand.core.store import DatasetStore


class Dataset:
    """DuckDB-backed dataset handle for notebooks."""

    def __init__(self, dataset_id: str, client: DuckDBClient):
        self.dataset_id = dataset_id
        self.client = client
        self.queries = CommonQueries(client)

    @property
    def tables(self) -> list[str]:
        return self.client.table_names()

    def schema(self) -> dict[str, list[dict[str, str]]]:
        return self.client.schema()

    def profile(self, table: str | None = None) -> pd.DataFrame:
        table = table or self._default_table()
        return self.queries.profile(table)

    def sql(self, query: str) -> pd.DataFrame:
        return self.client.to_dataframe(query)

    def add(
        self,
        path: str | Path,
        *,
        table_name: str | None = None,
        replace: bool = False,
    ) -> list[str]:
        """Add another spreadsheet into this dataset."""
        result = ingest_file(
            path,
            dataset_id=self.dataset_id,
            db_path=self.client.path,
            client=self.client,
            table_name=table_name,
            if_exists="replace" if replace else "fail",
        )
        return [t.name for t in result.tables]

    def join(
        self,
        left: str | None = None,
        right: str | None = None,
        *,
        on: str | list[str] | list[JoinKey] | None = None,
        left_on: str | list[str] | None = None,
        right_on: str | list[str] | None = None,
        how: str = "inner",
        select: list[str] | None = None,
        as_table: str | None = None,
        limit: int | None = None,
        spec: JoinSpec | None = None,
        plan: JoinPlan | None = None,
    ) -> pd.DataFrame:
        """Join tables with an explicit key mapping.

        Examples::

            ds.join("sales", "customers", on="customer_id")
            ds.join("sales", "customers", on=["order_cust_id=id"])
            ds.join(left="sales", right="customers", left_on="cust_id", right_on="id", how="left")
            ds.join(plan=JoinPlan(steps=[...], as_table="enriched"))
        """
        if plan is not None:
            df, _ = execute_join_plan(self.client, plan)
            return df
        if spec is not None:
            df, _ = execute_join(self.client, spec)
            return df
        if not left or not right:
            raise ValueError("Provide left/right or a JoinSpec/JoinPlan")

        keys: list[str | JoinKey]
        if left_on is not None or right_on is not None:
            lk = [left_on] if isinstance(left_on, str) else list(left_on or [])
            rk = [right_on] if isinstance(right_on, str) else list(right_on or [])
            if len(lk) != len(rk) or not lk:
                raise ValueError("left_on and right_on must be the same non-zero length")
            keys = [JoinKey(left=a, right=b) for a, b in zip(lk, rk, strict=True)]
        elif on is None:
            raise ValueError("Provide on= or left_on=/right_on=")
        elif isinstance(on, str):
            keys = [on]
        else:
            keys = list(on)

        join_spec = JoinSpec(
            left=left,
            right=right,
            on=keys,
            how=how,  # type: ignore[arg-type]
            select=select,
            as_table=as_table,
            limit=limit,
        )
        df, _ = execute_join(self.client, join_spec)
        return df

    def chart(
        self,
        df: pd.DataFrame | None = None,
        *,
        sql: str | None = None,
        chart_type: ChartType | None = None,
        title: str | None = None,
        spec: ChartSpec | None = None,
    ) -> Any:
        if df is None:
            if not sql:
                raise ValueError("Provide df or sql")
            df = self.client.to_dataframe(sql)
        chart_spec = spec or plan_chart(df, preferred=chart_type, title=title)
        fig = render_figure(df, chart_spec)
        try:
            from IPython.display import display

            display(fig)
        except Exception:
            pass
        return fig

    def ask(self, message: str, *, chart_type: ChartType | None = None) -> dict[str, Any]:
        result = NLSQLChat(self.client).ask(message, chart_type=chart_type)
        fig = None
        try:
            df = self.client.to_dataframe(result.sql)
            preferred = (result.chart.get("spec") or {}).get("chart_type")
            fig = self.chart(df, chart_type=preferred, title=result.summary[:80])
        except Exception:
            fig = None
        return {
            "summary": result.summary,
            "sql": result.sql,
            "sql_preview": result.sql_preview,
            "row_count": result.row_count,
            "is_preview": result.is_preview,
            "full_row_count": result.full_row_count,
            "preview": result.preview,
            "chart": result.chart,
            "figure": fig,
        }

    def _default_table(self) -> str:
        tables = self.tables
        if not tables:
            raise ValueError("Dataset has no tables")
        return tables[0]

    def close(self) -> None:
        from sand.db.pool import close_client

        if self.client.owns_connection:
            self.client.close()
        else:
            close_client(self.client.path)


def load(
    path: str | Path | Sequence[str | Path],
    *,
    dataset_id: str | None = None,
) -> Dataset:
    """Ingest one or more spreadsheets (or open an existing dataset id)."""
    from sand.db.pool import get_client

    settings = get_settings()
    native = {".csv", ".xlsx", ".xls", ".parquet"}

    if isinstance(path, (str, Path)):
        path_obj = Path(path)
        if path_obj.suffix.lower() in native:
            ds_id = dataset_id or path_obj.stem
            result = ingest_file(path_obj, dataset_id=ds_id, db_path=settings.db_path(ds_id))
            client = get_client(result.db_path, read_only=False)
            return Dataset(result.dataset_id, client)
        store = DatasetStore(settings)
        ds_id = dataset_id or str(path)
        client = store.open(ds_id)
        return Dataset(ds_id, client)

    paths = [Path(p) for p in path]
    if not paths:
        raise ValueError("Provide at least one spreadsheet path")
    ds_id = dataset_id or paths[0].stem
    result = ingest_files(paths, dataset_id=ds_id, db_path=settings.db_path(ds_id))
    client = get_client(result.db_path, read_only=False)
    return Dataset(result.dataset_id, client)
