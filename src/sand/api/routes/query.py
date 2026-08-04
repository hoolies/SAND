"""Structured common-query and ad-hoc SQL endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from sand.api.errors import dataset_client, error_detail, http_error_from_exc, tabular_result
from sand.charts.planner import plan_chart
from sand.charts.plotly_renderer import render_bundle
from sand.charts.specs import ChartSpec, ChartType
from sand.core.limits import estimate_sql_rows, guard_result_rows, limits_from_settings
from sand.llm.nlsql import EVAL_LIMIT, assert_readonly_sql, with_eval_limit
from sand.queries.common import CommonQueries

router = APIRouter()


class CommonQueryRequest(BaseModel):
    dataset_id: str
    action: Literal["profile", "filter", "groupby", "top_n", "missing", "time_series", "join"]
    table: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class SqlRequest(BaseModel):
    dataset_id: str
    sql: str
    chart_type: ChartType | None = None
    chart: ChartSpec | None = None
    run_full: bool = False
    bypass_chart_cap: bool = False


@router.post("/sql")
def run_sql(
    body: SqlRequest,
    x_sand_query_id: str | None = Header(default=None, alias="X-SAND-Query-Id"),
) -> dict:
    """Run read-only SQL without an LLM (preview-first, optional chart)."""
    try:
        with dataset_client(
            body.dataset_id,
            read_only=True,
            track=True,
            query_id=x_sand_query_id,
        ) as client:
            limits = limits_from_settings()
            sql = assert_readonly_sql(body.sql, allowed_tables=client.table_names())
            sql_preview = with_eval_limit(sql, EVAL_LIMIT)
            default_cap = max(EVAL_LIMIT, int(limits.chart_sample_rows))
            chart_cap = int(limits.max_result_rows) if body.bypass_chart_cap else default_cap
            if body.run_full:
                guard_result_rows(client, sql, max_rows=limits.max_result_rows, action="sql query")
                run_sql_text = with_eval_limit(sql, min(chart_cap, limits.max_result_rows))
            else:
                run_sql_text = sql_preview
            df = client.to_dataframe(run_sql_text)
            if body.chart is not None:
                spec = body.chart
            else:
                spec = plan_chart(df, preferred=body.chart_type, title="SQL result")
            bundle = render_bundle(df, spec)
            full_n = None
            if body.run_full:
                try:
                    full_n = estimate_sql_rows(client, sql, probe_limit=limits.max_result_rows)
                except Exception:
                    full_n = bundle["row_count"]
            return {
                "dataset_id": body.dataset_id,
                "sql": sql,
                "sql_preview": sql_preview,
                "summary": "SQL result" + (" (full sample)" if body.run_full else " (preview)"),
                "chart": bundle,
                "preview": bundle["preview"],
                "row_count": bundle["row_count"],
                "columns": bundle["columns"],
                "is_preview": not body.run_full,
                "evaluated_limit": EVAL_LIMIT if not body.run_full else min(chart_cap, bundle["row_count"]),
                "full_row_count": full_n if full_n is not None else bundle["row_count"],
                "chart_sample_rows": chart_cap if body.run_full else limits.chart_sample_rows,
                "chart_capped": bool(
                    body.run_full and full_n is not None and bundle["row_count"] < int(full_n)
                ),
                "max_result_rows": limits.max_result_rows,
            }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc


@router.post("/common")
def common_query(body: CommonQueryRequest) -> dict:
    """Run a structured common query.

    ``action=join`` is deprecated — use ``POST /query/join`` instead (returns 410).
    """
    if body.action == "join":
        raise HTTPException(
            status_code=410,
            detail=error_detail(
                "deprecated",
                "POST /query/common action=join is deprecated. Use POST /query/join with an explicit JoinSpec.",
                replacement="POST /query/join",
            ),
        )

    try:
        with dataset_client(body.dataset_id, read_only=True) as client:
            q = CommonQueries(client)
            if body.action == "profile":
                if not body.table:
                    raise ValueError("table is required")
                df = q.profile(body.table)
            elif body.action == "filter":
                if not body.table:
                    raise ValueError("table is required")
                df = q.filter_rows(
                    body.table,
                    filters=body.params.get("filters"),
                    order_by=body.params.get("order_by"),
                    ascending=body.params.get("ascending", True),
                    limit=int(body.params.get("limit", 100)),
                    where=body.params.get("where"),
                )
            elif body.action == "groupby":
                if not body.table:
                    raise ValueError("table is required")
                df = q.groupby(body.table, **body.params)
            elif body.action == "top_n":
                if not body.table:
                    raise ValueError("table is required")
                df = q.top_n(body.table, **body.params)
            elif body.action == "missing":
                if not body.table:
                    raise ValueError("table is required")
                df = q.missing_report(body.table)
            elif body.action == "time_series":
                if not body.table:
                    raise ValueError("table is required")
                df = q.time_series(body.table, **body.params)
            else:
                raise ValueError(f"Unknown action: {body.action}")
            return tabular_result(
                dataset_id=body.dataset_id,
                df=df,
                action=body.action,
                table=body.table,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
