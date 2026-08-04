"""Chat / NL→SQL endpoint with preview-first evaluation and memory."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sand.api.errors import dataset_client, http_error_from_exc, open_dataset, tabular_result
from sand.charts.specs import ChartSpec, ChartType
from sand.core.dataset_meta import clear_chat, list_chat
from sand.llm.nlsql import NLSQLChat
from sand.queries.common import AggFunc, CommonQueries

router = APIRouter()


class ChatRequest(BaseModel):
    dataset_id: str
    message: str
    chart_type: ChartType | None = None
    chart: ChartSpec | None = None
    run_full: bool = False
    sql: str | None = None


class TopNAskParams(BaseModel):
    column: str | None = None
    n: int = Field(default=10, ge=1)
    ascending: bool = False


class GroupByAskParams(BaseModel):
    group_by: str | list[str] | None = None
    metric: str | None = None
    agg: AggFunc = "sum"
    limit: int = Field(default=50, ge=1)


class TimeSeriesAskParams(BaseModel):
    date_column: str | None = None
    metric: str | None = None
    agg: AggFunc = "sum"
    bucket: Literal["day", "month", "year"] = "month"


class CommonAskRequest(BaseModel):
    dataset_id: str
    action: Literal["profile", "missing", "top_n", "groupby", "time_series"]
    table: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("")
def chat(body: ChatRequest) -> dict:
    client = open_dataset(body.dataset_id)

    try:
        result = NLSQLChat(client).ask(
            body.message,
            chart_type=body.chart_type,
            chart_override=body.chart,
            run_full=body.run_full,
            sql_override=body.sql,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc

    return {
        "dataset_id": body.dataset_id,
        "summary": result.summary,
        "sql": result.sql,
        "sql_preview": result.sql_preview,
        "chart": result.chart,
        "preview": result.preview,
        "row_count": result.row_count,
        "is_preview": result.is_preview,
        "evaluated_limit": result.evaluated_limit,
        "full_row_count": result.full_row_count,
    }


@router.get("/{dataset_id}/history")
def chat_history(dataset_id: str, limit: int = 50) -> dict:
    try:
        with dataset_client(dataset_id, read_only=True) as client:
            turns = list_chat(client, limit=limit)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "turns": [t.model_dump() for t in turns]}


@router.delete("/{dataset_id}/history")
def chat_clear(dataset_id: str) -> dict:
    try:
        client = open_dataset(dataset_id)
        clear_chat(client)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "cleared": True}


@router.post("/common-ask")
def common_ask(body: CommonAskRequest) -> dict:
    """LLM-free shortcuts: profile, missing, top_n, groupby, time_series."""
    try:
        with dataset_client(body.dataset_id, read_only=True) as client:
            params = body.params or {}
            q = CommonQueries(client)
            table = body.table or (client.table_names()[0] if client.table_names() else None)
            if not table:
                raise ValueError("No tables in dataset")
            schema_cols = [c["name"] for c in client.schema().get(table, [])]

            if body.action == "profile":
                df = q.profile(table)
            elif body.action == "missing":
                df = q.missing_report(table)
            elif body.action == "top_n":
                p = TopNAskParams.model_validate(params)
                column = p.column or (schema_cols[-1] if schema_cols else None)
                if not column:
                    raise ValueError("column is required for top_n")
                df = q.top_n(table, column=column, n=p.n, ascending=p.ascending)
            elif body.action == "groupby":
                p = GroupByAskParams.model_validate(params)
                group_by = p.group_by
                if isinstance(group_by, str):
                    group_by = [group_by]
                metric = p.metric
                if not group_by:
                    group_by = [schema_cols[0]] if schema_cols else None
                if not metric:
                    metric = schema_cols[-1] if len(schema_cols) > 1 else None
                if not group_by or not metric:
                    raise ValueError("groupby needs group_by and metric columns")
                df = q.groupby(
                    table,
                    group_by=list(group_by),
                    metric=metric,
                    agg=p.agg,
                    limit=p.limit,
                )
            elif body.action == "time_series":
                p = TimeSeriesAskParams.model_validate(params)
                date_column = p.date_column or next(
                    (c for c in schema_cols if "date" in c.lower() or "time" in c.lower()),
                    None,
                )
                metric = p.metric or (schema_cols[-1] if schema_cols else None)
                if not date_column or not metric:
                    raise ValueError("time_series needs date_column and metric")
                df = q.time_series(
                    table,
                    date_column=date_column,
                    metric=metric,
                    agg=p.agg,
                    bucket=p.bucket,
                )
            else:
                raise ValueError("action must be profile, missing, top_n, groupby, or time_series")

            return tabular_result(
                dataset_id=body.dataset_id,
                df=df,
                action=body.action,
                table=table,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
