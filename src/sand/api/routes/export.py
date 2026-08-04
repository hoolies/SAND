"""Export query results or whole databases."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from sand.api.errors import http_error_from_exc, open_dataset
from sand.core.limits import guard_result_rows, limits_from_settings
from sand.llm.nlsql import assert_readonly_sql
from sand.core.store import DatasetStore

router = APIRouter()


class ExportRequest(BaseModel):
    dataset_id: str
    sql: str | None = None
    table: str | None = None
    format: Literal["csv", "xlsx", "db"] = "csv"


@router.post("/{fmt}")
def export_result(fmt: Literal["csv", "xlsx", "db"], body: ExportRequest) -> StreamingResponse:
    body.format = fmt
    store = DatasetStore()

    if body.format == "db":
        try:
            raw = store.export_bytes(body.dataset_id)
        except Exception as exc:  # noqa: BLE001
            raise http_error_from_exc(exc) from exc
        data = io.BytesIO(raw)
        return StreamingResponse(
            data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{body.dataset_id}.duckdb"'},
        )

    limits = limits_from_settings()
    tmp_csv: Path | None = None
    try:
        client = open_dataset(body.dataset_id, store=store, read_only=True)
        try:
            if body.sql:
                sql = assert_readonly_sql(body.sql, allowed_tables=client.table_names())
            elif body.table:
                if body.table not in client.table_names():
                    raise ValueError(f"Unknown table: {body.table}")
                sql = f'SELECT * FROM "{body.table}"'
            else:
                raise ValueError("Provide sql or table for csv/xlsx export")
            guard_result_rows(client, sql, max_rows=limits.max_export_rows, action="export")

            if body.format == "csv":
                tmp = tempfile.NamedTemporaryFile(prefix="sand_export_", suffix=".csv", delete=False)
                tmp_csv = Path(tmp.name)
                tmp.close()
                client.copy_to_csv(sql, tmp_csv)

                def _iter_file():
                    try:
                        with tmp_csv.open("rb") as fh:
                            while True:
                                chunk = fh.read(1024 * 64)
                                if not chunk:
                                    break
                                yield chunk
                    finally:
                        tmp_csv.unlink(missing_ok=True)

                return StreamingResponse(
                    _iter_file(),
                    media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{body.dataset_id}.csv"'},
                )

            # XLSX: chunked fetch via pandas still, but row-guarded
            df = client.to_dataframe(sql)
            data = io.BytesIO()
            df.to_excel(data, index=False)
            data.seek(0)
            return StreamingResponse(
                data,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{body.dataset_id}.xlsx"'},
            )
        finally:
            if client.owns_connection:
                client.close()
    except HTTPException:
        if tmp_csv is not None:
            tmp_csv.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        if tmp_csv is not None:
            tmp_csv.unlink(missing_ok=True)
        raise http_error_from_exc(exc) from exc
