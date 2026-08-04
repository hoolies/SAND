"""Chat / NL→SQL endpoint with preview-first evaluation and memory."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from sand.api.errors import dataset_client, error_detail, http_error_from_exc, open_dataset, tabular_result
from sand.charts.specs import ChartSpec, ChartType
from sand.core.chat_store import clear_chat, list_chat
from sand.core.dataset_meta import (
    clear_view_cache,
    delete_view,
    get_view,
    get_view_cache,
    list_views,
    save_view,
    set_view_cache,
)
from sand.core.limits import limits_from_settings
from sand.core.store import DatasetStore
from sand.db.active_queries import interrupt as interrupt_queries
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
    bypass_chart_cap: bool = False


class CancelRequest(BaseModel):
    dataset_id: str
    query_id: str | None = None


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
    action: Literal["profile", "missing", "top_n", "groupby", "time_series", "filter"]
    table: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ViewSaveRequest(BaseModel):
    dataset_id: str
    name: str
    sql: str
    chart_type: ChartType | None = None
    cache_enabled: bool = False
    allow_over_cap: bool = False


class ViewRunRequest(BaseModel):
    dataset_id: str
    name: str
    use_cache: bool = True
    refresh_cache: bool = False


def _chat_payload(dataset_id: str, result: Any) -> dict[str, Any]:
    limits = limits_from_settings()
    return {
        "dataset_id": dataset_id,
        "summary": result.summary,
        "sql": result.sql,
        "sql_preview": result.sql_preview,
        "chart": result.chart,
        "preview": result.preview,
        "row_count": result.row_count,
        "is_preview": result.is_preview,
        "evaluated_limit": result.evaluated_limit,
        "full_row_count": result.full_row_count,
        "chart_sample_rows": result.chart_sample_rows or limits.chart_sample_rows,
        "chart_capped": result.chart_capped,
        "max_result_rows": limits.max_result_rows,
    }


@router.post("")
def chat(
    body: ChatRequest,
    x_sand_query_id: str | None = Header(default=None, alias="X-SAND-Query-Id"),
) -> dict:
    try:
        with dataset_client(
            body.dataset_id,
            read_only=True,
            track=True,
            query_id=x_sand_query_id,
        ) as client:
            result = NLSQLChat(client, dataset_id=body.dataset_id).ask(
                body.message,
                chart_type=body.chart_type,
                chart_override=body.chart,
                run_full=body.run_full,
                sql_override=body.sql,
                bypass_chart_cap=body.bypass_chart_cap,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc

    return _chat_payload(body.dataset_id, result)


@router.post("/cancel")
def chat_cancel(body: CancelRequest) -> dict:
    """Interrupt an in-flight DuckDB query for this dataset (best-effort)."""
    try:
        DatasetStore().get_path(body.dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=error_detail("not_found", str(exc))) from exc
    count = interrupt_queries(query_id=body.query_id, dataset_id=body.dataset_id)
    return {"dataset_id": body.dataset_id, "interrupted": count, "query_id": body.query_id}


@router.get("/{dataset_id}/history")
def chat_history(dataset_id: str, limit: int = 50) -> dict:
    try:
        store = DatasetStore()
        store.get_path(dataset_id)  # 404 if missing
        with dataset_client(dataset_id, store=store, read_only=True) as client:
            turns = list_chat(dataset_id, limit=limit, migrate_from=client)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "turns": [t.model_dump() for t in turns]}


@router.delete("/{dataset_id}/history")
def chat_clear(dataset_id: str) -> dict:
    try:
        store = DatasetStore()
        store.get_path(dataset_id)
        clear_chat(dataset_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "cleared": True}


@router.get("/views/{dataset_id}")
def views_list(dataset_id: str) -> dict:
    try:
        with dataset_client(dataset_id, read_only=True) as client:
            views = list_views(client)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "views": [v.model_dump() for v in views]}


@router.post("/views")
def views_save(body: ViewSaveRequest) -> dict:
    try:
        client = open_dataset(body.dataset_id)
        view = save_view(
            client,
            body.name,
            body.sql,
            chart_type=body.chart_type,
            cache_enabled=body.cache_enabled,
            allow_over_cap=body.allow_over_cap,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": body.dataset_id, "view": view.model_dump()}


@router.post("/views/run")
def views_run(
    body: ViewRunRequest,
    x_sand_query_id: str | None = Header(default=None, alias="X-SAND-Query-Id"),
) -> dict:
    try:
        store = DatasetStore()
        store.get_path(body.dataset_id)

        if body.use_cache and not body.refresh_cache:
            with dataset_client(body.dataset_id, store=store, read_only=True) as client:
                view = get_view(client, body.name)
                if view is None:
                    raise HTTPException(
                        status_code=404,
                        detail=error_detail("not_found", f"Unknown view: {body.name}"),
                    )
                if view.cache_enabled:
                    cached = get_view_cache(client, view.name)
                    if cached is not None:
                        out = dict(cached)
                        out["from_cache"] = True
                        out["view"] = view.model_dump()
                        return out

        cache_updated = False
        with dataset_client(
            body.dataset_id,
            store=store,
            read_only=True,
            track=True,
            query_id=x_sand_query_id,
        ) as client:
            view = get_view(client, body.name)
            if view is None:
                raise HTTPException(
                    status_code=404,
                    detail=error_detail("not_found", f"Unknown view: {body.name}"),
                )
            result = NLSQLChat(client, dataset_id=body.dataset_id).ask(
                f"saved view: {view.name}",
                chart_type=view.chart_type,  # type: ignore[arg-type]
                run_full=True,
                sql_override=view.sql,
                persist=False,
                bypass_chart_cap=view.allow_over_cap,
            )
            payload = _chat_payload(body.dataset_id, result)
            payload["from_cache"] = False
            payload["view"] = view.model_dump()

        if view.cache_enabled and (body.refresh_cache or body.use_cache):
            write_client = open_dataset(body.dataset_id, store=store)
            try:
                set_view_cache(
                    write_client,
                    view.name,
                    {
                        k: v
                        for k, v in payload.items()
                        if k not in {"view", "from_cache", "cache_updated"}
                    },
                )
                refreshed = get_view(write_client, view.name)
                if refreshed is not None:
                    payload["view"] = refreshed.model_dump()
                cache_updated = True
            finally:
                if write_client.owns_connection:
                    write_client.close()
        payload["cache_updated"] = cache_updated
        return payload
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc


@router.delete("/views/{dataset_id}/{name}/cache")
def views_clear_cache(dataset_id: str, name: str) -> dict:
    try:
        client = open_dataset(dataset_id)
        if get_view(client, name) is None:
            raise HTTPException(status_code=404, detail=error_detail("not_found", "View not found"))
        clear_view_cache(client, name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"cleared": True}


@router.delete("/views/{dataset_id}/{name}")
def views_delete(dataset_id: str, name: str) -> dict:
    try:
        client = open_dataset(dataset_id)
        ok = delete_view(client, name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=error_detail("not_found", "View not found"))
    return {"deleted": True}


@router.post("/common-ask")
def common_ask(body: CommonAskRequest) -> dict:
    """LLM-free shortcuts: profile, missing, top_n, groupby, time_series, filter."""
    try:
        with dataset_client(body.dataset_id, read_only=True, track=True) as client:
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
            elif body.action == "filter":
                filters = params.get("filters")
                if not filters:
                    raise ValueError("filter needs params.filters (list of {column, op, value})")
                df = q.filter_rows(
                    table,
                    filters=filters,
                    order_by=params.get("order_by"),
                    ascending=params.get("ascending", True),
                    limit=int(params.get("limit", 100)),
                )
            elif body.action == "top_n":
                p = TopNAskParams.model_validate(params)
                column = p.column or (schema_cols[-1] if schema_cols else None)
                if not column:
                    raise ValueError("column is required for top_n")
                df = q.top_n(table, column=column, n=p.n, ascending=p.ascending)
            elif body.action == "groupby":
                gp = GroupByAskParams.model_validate(params)
                group_by = gp.group_by
                if isinstance(group_by, str):
                    group_by = [group_by]
                metric = gp.metric
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
                    agg=gp.agg,
                    limit=gp.limit,
                )
            elif body.action == "time_series":
                tp = TimeSeriesAskParams.model_validate(params)
                date_column = tp.date_column or next(
                    (c for c in schema_cols if "date" in c.lower() or "time" in c.lower()),
                    None,
                )
                metric = tp.metric or (schema_cols[-1] if schema_cols else None)
                if not date_column or not metric:
                    raise ValueError("time_series needs date_column and metric")
                df = q.time_series(
                    table,
                    date_column=date_column,
                    metric=metric,
                    agg=tp.agg,
                    bucket=tp.bucket,
                )
            else:
                raise ValueError("action must be profile, missing, top_n, groupby, time_series, or filter")

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
