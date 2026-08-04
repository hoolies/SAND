"""Dataset upload and schema endpoints."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from sand.api.errors import dataset_client, error_detail, http_error_from_exc, open_dataset
from sand.core.config import get_settings, sanitize_dataset_id
from sand.core.store import DatasetStore
from sand.ingest.loader import ingest_file, ingest_files, ingest_result_payload

router = APIRouter()


def _safe_dataset_id(value: str) -> str:
    try:
        return sanitize_dataset_id(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=error_detail("bad_request", str(exc))) from exc


@router.get("")
def list_datasets() -> dict:
    """List datasets with disk usage against SAND_MAX_DATA_DIR_BYTES."""
    from sand.core.limits import data_dir_usage_bytes, limits_from_settings

    store = DatasetStore()
    settings = get_settings()
    limits = limits_from_settings(settings)
    used = data_dir_usage_bytes(settings.data_dir)
    budget = limits.max_data_dir_bytes
    datasets = []
    for d in store.list_datasets():
        size = d.db_path.stat().st_size if d.db_path.exists() else 0
        chat = settings.data_dir / f"{d.id}.chat.jsonl"
        if chat.exists():
            size += chat.stat().st_size
        datasets.append(
            {
                "id": d.id,
                "db_path": str(d.db_path),
                "tables": d.tables,
                "size_bytes": size,
            }
        )
    orphans = [{"path": str(o.path), "stem": o.stem} for o in store.list_orphan_sqlite()]
    warn = None
    if budget > 0 and used >= budget * 0.8:
        warn = (
            f"Data dir is at {used:,} / {budget:,} bytes "
            f"({100.0 * used / budget:.0f}%). Delete datasets or raise SAND_MAX_DATA_DIR_BYTES."
        )
    return {
        "datasets": datasets,
        "orphans": orphans,
        "empty": len(datasets) == 0,
        "disk_usage_bytes": used,
        "disk_budget_bytes": budget if budget > 0 else None,
        "disk_warning": warn,
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
    if suffix not in {".csv", ".xlsx", ".parquet"}:
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


def _parse_sheets_form(sheets: str | None) -> list[str] | None:
    if not sheets or not str(sheets).strip():
        return None
    raw = str(sheets).strip()
    if raw.startswith("["):
        import json

        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("sheets must be a JSON list of sheet names")
        return [str(s) for s in data]
    return [s.strip() for s in raw.split(",") if s.strip()]


@router.post("/xlsx/sheets")
async def xlsx_sheet_names(file: UploadFile = File(...)) -> dict:
    """List sheet names in an uploaded XLSX without ingesting."""
    from sand.core.limits import limits_from_settings
    from sand.ingest.readers import list_xlsx_sheets

    limits = limits_from_settings()
    saved: Path | None = None
    try:
        if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
            raise HTTPException(
                status_code=400,
                detail=error_detail("bad_request", "Provide an .xlsx file"),
            )
        saved = await _save_upload(file, max_bytes=limits.max_ingest_bytes)
        sheets = list_xlsx_sheets(saved)
        return {"filename": file.filename, "sheets": sheets}
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


@router.post("/import")
async def import_duckdb_file(
    file: UploadFile = File(...),
    dataset_id: str | None = Form(default=None),
) -> dict:
    """Import an existing ``.duckdb`` file into the local data dir."""
    from sand.core.limits import limits_from_settings

    if not file.filename or Path(file.filename).suffix.lower() != ".duckdb":
        raise HTTPException(
            status_code=400,
            detail=error_detail("bad_request", "Provide a .duckdb file"),
        )
    ds_id = _safe_dataset_id(dataset_id or Path(file.filename).stem)
    limits = limits_from_settings()
    stem = sanitize_filename_stem(Path(file.filename).stem)
    tmp_dir = Path(tempfile.mkdtemp(prefix="sand_import_"))
    saved = tmp_dir / f"{stem}.duckdb"
    written = 0
    try:
        with saved.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limits.max_ingest_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=error_detail(
                            "limit_exceeded",
                            f"Import exceeds SAND_MAX_INGEST_BYTES={limits.max_ingest_bytes:,}",
                        ),
                    )
                fh.write(chunk)
        store = DatasetStore()
        path = store.import_duckdb(saved, ds_id)
        with dataset_client(ds_id, store=store, read_only=True) as client:
            tables = client.table_names()
        return {"dataset_id": ds_id, "db_path": str(path), "tables": tables}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    finally:
        saved.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


@router.post("/upload")
async def upload_dataset(
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    dataset_id: str | None = Form(default=None),
    replace: bool = Form(default=True),
    sheets: str | None = Form(default=None),
) -> dict:
    """Upload one or more spreadsheets into a dataset.

    Use ``file`` for a single upload, or ``files`` for multiple. When ``dataset_id``
    already exists, new tables are added (``replace`` controls name collisions).
    Optional ``sheets`` (JSON list or comma-separated) limits XLSX sheets ingested.
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
        sheet_filter = _parse_sheets_form(sheets)
        for upload in uploads:
            max_bytes = limits.max_ingest_bytes
            saved.append(await _save_upload(upload, max_bytes=max_bytes))
        result = ingest_files(
            saved,
            dataset_id=ds_id,
            db_path=settings.db_path(ds_id),
            if_exists="replace" if replace else "fail",
            xlsx_sheets=sheet_filter,
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
    sheets: str | None = Form(default=None),
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
        sheet_filter = _parse_sheets_form(sheets)
        max_bytes = limits.max_ingest_bytes
        saved = await _save_upload(file, max_bytes=max_bytes)
        result = ingest_file(
            saved,
            dataset_id=dataset_id,
            db_path=settings.db_path(dataset_id),
            table_name=table_name,
            if_exists="replace" if replace else "fail",
            xlsx_sheets=sheet_filter,
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
            lineage: dict[str, dict] = {}
            try:
                meta = client.metadata()
                for row in meta.to_dict(orient="records"):
                    name = row.get("table_name")
                    if not name:
                        continue
                    lineage[str(name)] = {
                        "source_file": row.get("source_file"),
                        "sheet_name": row.get("sheet_name"),
                        "row_count": row.get("row_count"),
                    }
            except Exception:
                lineage = {}
            return {
                "dataset_id": dataset_id,
                "schema": client.schema(),
                "tables": client.table_names(),
                "lineage": lineage,
            }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc


@router.get("/{dataset_id}/rows/{table}")
def dataset_table_rows(dataset_id: str, table: str, limit: int = 50, offset: int = 0) -> dict:
    """Paginated sample rows for the Data tab peek."""
    from sand.core.limits import limits_from_settings

    limits = limits_from_settings()
    limit = max(1, min(int(limit), min(500, limits.max_offline_ask_rows)))
    offset = max(0, int(offset))
    try:
        with dataset_client(dataset_id, read_only=True) as client:
            if table not in client.table_names():
                raise ValueError(f"Unknown table: {table}")
            total = int(client.fetchall(f'SELECT COUNT(*) FROM "{table}"')[0][0])
            df = client.to_dataframe(
                f'SELECT * FROM "{table}" LIMIT {limit} OFFSET {offset}'
            )
            columns = list(df.columns)
            rows = df.where(df.notnull(), None).to_dict(orient="records")
            return {
                "dataset_id": dataset_id,
                "table": table,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "offset": offset,
                "limit": limit,
                "total_rows": total,
            }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc


class RenameDatasetRequest(BaseModel):
    new_id: str


@router.post("/{dataset_id}/rename")
def rename_dataset(dataset_id: str, body: RenameDatasetRequest) -> dict:
    store = DatasetStore()
    try:
        path = store.rename(dataset_id, body.new_id)
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": path.stem, "db_path": str(path), "renamed_from": dataset_id}


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


@router.post("/{dataset_id}/checkpoint")
def checkpoint_dataset(dataset_id: str) -> dict:
    """Flush WAL into the main file (safe before copy/backup)."""
    try:
        client = open_dataset(dataset_id, read_only=False)
        client.checkpoint()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "checkpointed": True}


class RenameTableRequest(BaseModel):
    new_name: str


@router.post("/{dataset_id}/tables/{table}/rename")
def rename_table_endpoint(dataset_id: str, table: str, body: RenameTableRequest) -> dict:
    from sand.core.dataset_meta import rename_table

    try:
        client = open_dataset(dataset_id, read_only=False)
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
        client = open_dataset(dataset_id, read_only=False)
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
        client = open_dataset(dataset_id, read_only=False)
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
