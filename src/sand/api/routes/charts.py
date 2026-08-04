"""Explicit chart rendering endpoint."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sand.api.errors import error_detail, http_error_from_exc, open_dataset
from sand.charts.planner import plan_chart
from sand.charts.plotly_renderer import render_bundle
from sand.charts.specs import ChartSpec, ChartType
from sand.core.limits import guard_result_rows, limits_from_settings
from sand.llm.nlsql import assert_readonly_sql

router = APIRouter()


class ChartRenderRequest(BaseModel):
    dataset_id: str
    sql: str | None = None
    rows: list[dict[str, Any]] | None = None
    chart_type: ChartType | None = None
    chart: ChartSpec | None = None
    title: str | None = None


@router.post("/render")
def render_chart(body: ChartRenderRequest) -> dict:
    try:
        if body.rows is not None:
            df = pd.DataFrame(body.rows)
        elif body.sql:
            client = open_dataset(body.dataset_id, read_only=True)
            try:
                sql = assert_readonly_sql(body.sql, allowed_tables=client.table_names())
                limits = limits_from_settings()
                guard_result_rows(client, sql, max_rows=limits.max_result_rows, action="chart query")
                df = client.to_dataframe(sql)
            finally:
                if client.owns_connection:
                    client.close()
        else:
            raise HTTPException(
                status_code=400,
                detail=error_detail("bad_request", "Provide sql or rows"),
            )

        spec = body.chart or plan_chart(df, preferred=body.chart_type, title=body.title)
        return render_bundle(df, spec)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
