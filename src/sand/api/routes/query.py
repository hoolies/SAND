"""Structured common-query endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sand.api.errors import dataset_client, error_detail, http_error_from_exc, tabular_result
from sand.queries.common import CommonQueries

router = APIRouter()


class CommonQueryRequest(BaseModel):
    dataset_id: str
    action: Literal["profile", "filter", "groupby", "top_n", "missing", "time_series", "join"]
    table: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


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
