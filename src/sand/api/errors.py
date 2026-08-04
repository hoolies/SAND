"""Shared FastAPI error mapping and response helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from sand.db.duckdb_client import DuckDBClient
from sand.db.pool import DatabaseLockedError
from sand.core.limits import ResourceLimitError
from sand.llm.openai_compat import LLMNotConfiguredError
from sand.core.store import DatasetStore


def error_detail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    body.update({k: v for k, v in extra.items() if v is not None})
    return body


def open_dataset(
    dataset_id: str,
    *,
    store: DatasetStore | None = None,
    read_only: bool = False,
) -> DuckDBClient:
    store = store or DatasetStore()
    try:
        return store.open(dataset_id, read_only=read_only)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("not_found", str(exc)),
        ) from exc
    except DatabaseLockedError as exc:
        raise HTTPException(
            status_code=423,
            detail=error_detail("locked", str(exc)),
        ) from exc


@contextmanager
def dataset_client(
    dataset_id: str,
    *,
    store: DatasetStore | None = None,
    read_only: bool = False,
) -> Iterator[DuckDBClient]:
    """Open a dataset and close ephemeral read-only connections on exit."""
    client = open_dataset(dataset_id, store=store, read_only=read_only)
    try:
        yield client
    finally:
        if client.owns_connection:
            client.close()


def http_error_from_exc(exc: BaseException) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=error_detail("not_found", str(exc)))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=error_detail("conflict", str(exc)))
    if isinstance(exc, DatabaseLockedError):
        return HTTPException(status_code=423, detail=error_detail("locked", str(exc)))
    if isinstance(exc, ResourceLimitError):
        return HTTPException(status_code=413, detail=error_detail("limit_exceeded", str(exc)))
    if isinstance(exc, LLMNotConfiguredError):
        return HTTPException(
            status_code=503,
            detail=error_detail(
                "llm_not_configured",
                str(exc),
                offline_actions=["profile", "missing", "top_n", "groupby", "time_series"],
            ),
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return HTTPException(
            status_code=502,
            detail=error_detail(
                "llm_upstream",
                f"LLM endpoint returned HTTP {exc.response.status_code}: {exc.response.text[:300]}",
            ),
        )
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError)):
        return HTTPException(
            status_code=503,
            detail=error_detail(
                "llm_unreachable",
                f"Could not reach LLM endpoint: {exc}",
                offline_actions=["profile", "missing", "top_n", "groupby", "time_series"],
            ),
        )
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return HTTPException(status_code=400, detail=error_detail("bad_request", str(exc)))
    if isinstance(exc, TimeoutError):
        return HTTPException(status_code=504, detail=error_detail("timeout", str(exc)))
    return HTTPException(status_code=500, detail=error_detail("internal", str(exc)))


class TabularResult(BaseModel):
    """Shared shape for common-query / common-ask / join preview rows."""

    dataset_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    action: str | None = None
    table: str | None = None
    sql: str | None = None
    as_table: str | None = None
    estimate: dict[str, Any] | None = None
    spec: dict[str, Any] | None = None
    deprecated: str | None = Field(
        default=None,
        description="Present when the endpoint/action is deprecated",
    )


def tabular_result(
    *,
    dataset_id: str,
    df: Any,
    action: str | None = None,
    table: str | None = None,
    sql: str | None = None,
    as_table: str | None = None,
    estimate: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
    deprecated: str | None = None,
) -> dict[str, Any]:
    records = df.where(df.notnull(), None).to_dict(orient="records")
    payload = TabularResult(
        dataset_id=dataset_id,
        columns=list(df.columns),
        rows=records,
        row_count=len(df),
        action=action,
        table=table,
        sql=sql,
        as_table=as_table,
        estimate=estimate,
        spec=spec,
        deprecated=deprecated,
    )
    # Drop nulls for a cleaner JSON body
    return payload.model_dump(exclude_none=True)
