"""Dataset upload and schema endpoints."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from sand.api.errors import dataset_client, error_detail, http_error_from_exc, open_dataset
from sand.core.config import get_settings, sanitize_dataset_id
from sand.ingest.loader import ingest_file, ingest_files, ingest_result_payload
from sand.core.store import DatasetStore

router = APIRouter()


def _safe_dataset_id(value: str) -> str:
    try:
        return sanitize_dataset_id(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=error_detail("bad_request", str(exc))) from exc


@router.get("")
def list_datasets() -> dict:
    """List datasets.

    Response shape (not a bare array)::

        {
          "datasets": [{"id", "db_path", "tables"}, ...],
          "orphans": [{"path", "stem"}, ...],  # legacy *.db files
          "empty": bool,
          "hint": str | null
        }
    """
    store = DatasetStore()
    datasets = [{"id": d.id, "db_path": str(d.db_path), "tables": d.tables} for d in store.list_datasets()]
    orphans = [{"path": str(o.path), "stem": o.stem} for o in store.list_orphan_sqlite()]
    return {
        "datasets": datasets,
        "orphans": orphans,
        "empty": len(datasets) == 0,
        "hint": "Load the sample shop dataset or upload CSV/Excel/Parquet files to get started."
        if not datasets
        else None,
    }


def _result_payload(result) -> dict:
    return ingest_result_payload(result)


async def _save_upload(upload: UploadFile, *, max_bytes: int) -> Path:
    """Stream upload to disk, enforcing size before the file is fully buffered in memory."""
    if not upload.filename:
        raise HTTPException(status_code=400, detail=error_detail("bad_request", "Missing filename"))
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls", ".parquet"}:
        raise HTTPException(status_code=400, detail=f"Unsupported type for {upload.filename}")

    stem = sanitize_filename_stem(Path(upload.filename).stem)
    tmp_dir = Path(tempfile.mkdtemp(prefix="sand_upload_"))
    dest = tmp_dir / f"{stem}{suffix}"
    written = 0
    chunk_size = 1024 * 1024
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=error_detail(
                            "limit_exceeded",
                            f"{upload.filename} exceeds the upload limit of {max_bytes:,} bytes. "
                            f"Raise SAND_MAX_INGEST_BYTES or split the file.",
                        ),
                    )
                fh.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
        raise
    return dest


def sanitize_filename_stem(stem: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_-]+", "_", stem).strip("_")
    return cleaned or "sheet"


@router.post("/upload")
async def upload_dataset(
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    dataset_id: str | None = Form(default=None),
    replace: bool = Form(default=True),
) -> dict:
    """Upload one or more spreadsheets into a dataset.

    Use ``file`` for a single upload, or ``files`` for multiple. When ``dataset_id``
    already exists, new tables are added (``replace`` controls name collisions).
    """
    from sand.core.limits import limits_from_settings

    uploads: list[UploadFile] = []
    if files:
        uploads.extend([f for f in files if f.filename])
    if file is not None and file.filename:
        uploads.append(file)
    if not uploads:
        raise HTTPException(status_code=400, detail=error_detail("bad_request", "Provide file or files"))

    ds_id = _safe_dataset_id(dataset_id or Path(uploads[0].filename or "dataset").stem)
    settings = get_settings()
    limits = limits_from_settings(settings)
    saved: list[Path] = []

    try:
        for upload in uploads:
            suffix = Path(upload.filename or "").suffix.lower()
            max_bytes = limits.excel_pandas_max_bytes if suffix == ".xls" else limits.max_ingest_bytes
            saved.append(await _save_upload(upload, max_bytes=max_bytes))
        result = ingest_files(
            saved,
            dataset_id=ds_id,
            db_path=settings.db_path(ds_id),
            if_exists="replace" if replace else "fail",
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    finally:
        for path in saved:
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass

    return _result_payload(result)


@router.post("/{dataset_id}/tables")
async def add_table(
    dataset_id: str,
    file: UploadFile = File(...),
    table_name: str | None = Form(default=None),
    replace: bool = Form(default=False),
) -> dict:
    """Add another spreadsheet into an existing dataset."""
    from sand.core.limits import limits_from_settings

    dataset_id = _safe_dataset_id(dataset_id)
    store = DatasetStore()
    if not store.exists(dataset_id):
        raise HTTPException(
            status_code=404,
            detail=error_detail("not_found", f"Dataset not found: {dataset_id}"),
        )

    settings = get_settings()
    limits = limits_from_settings(settings)
    saved: Path | None = None
    try:
        suffix = Path(file.filename or "").suffix.lower()
        max_bytes = limits.excel_pandas_max_bytes if suffix == ".xls" else limits.max_ingest_bytes
        saved = await _save_upload(file, max_bytes=max_bytes)
        result = ingest_file(
            saved,
            dataset_id=dataset_id,
            db_path=settings.db_path(dataset_id),
            table_name=table_name,
            if_exists="replace" if replace else "fail",
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    finally:
        if saved is not None:
            saved.unlink(missing_ok=True)
            try:
                saved.parent.rmdir()
            except OSError:
                pass

    return _result_payload(result)


@router.delete("/orphans/{stem}")
def delete_orphan(stem: str) -> dict:
    """Delete a legacy SQLite ``*.db`` leftover (not a DuckDB dataset)."""
    store = DatasetStore()
    try:
        path = store.delete_orphan_sqlite(stem)
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"deleted": path.name}


@router.get("/{dataset_id}/schema")
def dataset_schema(dataset_id: str) -> dict:
    try:
        with dataset_client(dataset_id, read_only=True) as client:
            return {"dataset_id": dataset_id, "schema": client.schema(), "tables": client.table_names()}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc


@router.get("/{dataset_id}/profile/{table}")
def dataset_table_profile(dataset_id: str, table: str) -> dict:
    from sand.queries.common import CommonQueries

    try:
        with dataset_client(dataset_id, read_only=True) as client:
            df = CommonQueries(client).profile(table)
            samples = {}
            for col in client.schema().get(table, []):
                name = col["name"]
                sample_df = client.to_dataframe(
                    f'SELECT DISTINCT "{name}" AS v FROM "{table}" WHERE "{name}" IS NOT NULL LIMIT 5'
                )
                samples[name] = sample_df["v"].tolist()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {
        "dataset_id": dataset_id,
        "table": table,
        "profile": df.where(df.notnull(), None).to_dict(orient="records"),
        "samples": samples,
    }


@router.post("/samples/shop")
def load_shop_sample(dataset_id: str = "shop") -> dict:
    from sand.samples import load_sample_shop

    try:
        return load_sample_shop(dataset_id=_safe_dataset_id(dataset_id))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str) -> dict:
    store = DatasetStore()
    try:
        store.delete(dataset_id)
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"deleted": dataset_id}


@router.post("/{dataset_id}/duplicate")
def duplicate_dataset(dataset_id: str, new_id: str) -> dict:
    store = DatasetStore()
    try:
        path = store.duplicate(dataset_id, new_id)
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": path.stem, "db_path": str(path)}


class RenameTableRequest(BaseModel):
    new_name: str


@router.post("/{dataset_id}/tables/{table}/rename")
def rename_table_endpoint(dataset_id: str, table: str, body: RenameTableRequest) -> dict:
    from sand.core.dataset_meta import rename_table

    try:
        client = open_dataset(dataset_id)
        new_name = rename_table(client, table, body.new_name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "old": table, "new": new_name}


@router.delete("/{dataset_id}/tables/{table}")
def drop_table_endpoint(dataset_id: str, table: str) -> dict:
    from sand.core.dataset_meta import drop_table

    try:
        client = open_dataset(dataset_id)
        drop_table(client, table)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "dropped": table}


@router.get("/{dataset_id}/types/{table}")
def table_types(dataset_id: str, table: str) -> dict:
    from sand.ingest.typing import build_type_plan

    try:
        with dataset_client(dataset_id, read_only=True) as client:
            if table not in client.table_names():
                raise ValueError(f"Unknown table: {table}")
            df = client.to_dataframe(f'SELECT * FROM "{table}" LIMIT 5000')
            plan = build_type_plan(table, df)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "plan": plan.model_dump()}


class ApplyTypesBody(BaseModel):
    columns: list[dict]


@router.post("/{dataset_id}/types/{table}")
def apply_table_types(dataset_id: str, table: str, body: ApplyTypesBody) -> dict:
    from sand.ingest.typing import ColumnTypeSpec, TableTypePlan, apply_types_sql, build_type_plan

    try:
        client = open_dataset(dataset_id)
        if table not in client.table_names():
            raise ValueError(f"Unknown table: {table}")
        sample = client.to_dataframe(f'SELECT * FROM "{table}" LIMIT 5000')
        base = build_type_plan(table, sample)
        overrides = {c["name"]: c.get("type") for c in body.columns}
        cols = []
        for col in base.columns:
            override = overrides.get(col.name)
            cols.append(
                ColumnTypeSpec(
                    name=col.name,
                    inferred=col.inferred,
                    override=override,
                    nullable=col.nullable,
                    sample_values=col.sample_values,
                    null_count=col.null_count,
                    distinct_count=col.distinct_count,
                )
            )
        plan = TableTypePlan(table_name=table, columns=cols)
        n = apply_types_sql(client, table, plan)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "table": table, "plan": plan.model_dump(), "row_count": n}
