"""Load one or more spreadsheets into DuckDB."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sand.core.limits import check_data_dir_budget, check_file_size, limits_from_settings
from sand.db.duckdb_client import DuckDBClient, sanitize_table_name
from sand.db.pool import get_client
from sand.ingest.readers import SUPPORTED_EXTENSIONS, list_xlsx_sheets, read_spreadsheet


@dataclass
class TableInfo:
    name: str
    sheet_name: str
    row_count: int
    columns: list[str] = field(default_factory=list)
    source_file: str = ""


@dataclass
class IngestResult:
    dataset_id: str
    db_path: Path
    tables: list[TableInfo]
    source_files: list[str] = field(default_factory=list)

    @property
    def source_file(self) -> str:
        """Back-compat: first source file."""
        return self.source_files[0] if self.source_files else ""


def ingest_result_payload(result: IngestResult, **extra: object) -> dict:
    """Canonical ingest/sample response shape (shared by upload and samples)."""
    payload: dict = {
        "dataset_id": result.dataset_id,
        "db_path": str(result.db_path),
        "source_files": result.source_files,
        "tables": [
            {
                "name": t.name,
                "sheet_name": t.sheet_name,
                "row_count": t.row_count,
                "columns": t.columns,
                "source_file": t.source_file,
            }
            for t in result.tables
        ],
    }
    payload.update(extra)
    return payload


def _allocate_table_name(
    base: str,
    used: set[str],
    existing: set[str],
    *,
    if_exists: str,
) -> str:
    if base in used:
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        return name

    if base in existing and if_exists == "fail":
        raise ValueError(f"Table '{base}' already exists; pass if_exists='replace' or choose another name")

    used.add(base)
    return base


def ingest_file(
    path: str | Path,
    *,
    dataset_id: str | None = None,
    db_path: str | Path | None = None,
    client: DuckDBClient | None = None,
    table_name: str | None = None,
    if_exists: str = "replace",
) -> IngestResult:
    """Ingest a spreadsheet file into a DuckDB database."""
    return ingest_files(
        [path],
        dataset_id=dataset_id,
        db_path=db_path,
        client=client,
        table_names=[table_name] if table_name else None,
        if_exists=if_exists,
    )


def ingest_files(
    paths: list[str | Path],
    *,
    dataset_id: str | None = None,
    db_path: str | Path | None = None,
    client: DuckDBClient | None = None,
    table_names: list[str | None] | None = None,
    if_exists: str = "replace",
) -> IngestResult:
    """Ingest multiple spreadsheet files into one dataset.

    CSV / Parquet / XLSX use DuckDB-native readers. Legacy ``.xls`` still uses
    pandas with a stricter size guard.
    """
    if not paths:
        raise ValueError("At least one spreadsheet path is required")
    if if_exists not in {"replace", "fail"}:
        raise ValueError("if_exists must be 'replace' or 'fail'")

    limits = limits_from_settings()
    path_objs = [Path(p) for p in paths]
    for path in path_objs:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type '{ext}'")
        max_bytes = limits.excel_pandas_max_bytes if ext == ".xls" else limits.max_ingest_bytes
        check_file_size(path, max_bytes=max_bytes, label=path.name)

    # Budget for new on-disk footprint (uploads already on disk; still caps growth)
    check_data_dir_budget(additional_bytes=sum(p.stat().st_size for p in path_objs))

    dataset_id = dataset_id or path_objs[0].stem
    from sand.core.config import sanitize_dataset_id

    dataset_id = sanitize_dataset_id(dataset_id)
    owns_client = client is None

    if client is None:
        if db_path is None:
            from sand.core.config import get_settings

            db_path = get_settings().db_path(dataset_id)
        client = get_client(Path(db_path), read_only=False)

    if table_names is not None and len(table_names) != len(path_objs):
        raise ValueError("table_names must match the number of paths")

    tables: list[TableInfo] = []
    used_names: set[str] = set()
    existing = set(client.table_names())
    source_files: list[str] = []

    for idx, path in enumerate(path_objs):
        source_files.append(str(path))
        override = table_names[idx] if table_names else None
        ext = path.suffix.lower()

        if ext == ".csv":
            base = sanitize_table_name(override or path.stem)
            name = _allocate_table_name(base, used_names, existing, if_exists=if_exists)
            row_count, columns = client.ingest_csv(path, name, if_exists="replace")
            client.register_table(
                name,
                source_file=str(path),
                sheet_name=path.stem,
                original_columns=columns,
                row_count=row_count,
            )
            existing.add(name)
            tables.append(
                TableInfo(
                    name=name,
                    sheet_name=path.stem,
                    row_count=row_count,
                    columns=columns,
                    source_file=str(path),
                )
            )
            continue

        if ext == ".parquet":
            base = sanitize_table_name(override or path.stem)
            name = _allocate_table_name(base, used_names, existing, if_exists=if_exists)
            row_count, columns = client.ingest_parquet(path, name, if_exists="replace")
            client.register_table(
                name,
                source_file=str(path),
                sheet_name=path.stem,
                original_columns=columns,
                row_count=row_count,
            )
            existing.add(name)
            tables.append(
                TableInfo(
                    name=name,
                    sheet_name=path.stem,
                    row_count=row_count,
                    columns=columns,
                    source_file=str(path),
                )
            )
            continue

        if ext == ".xlsx":
            sheet_names = list_xlsx_sheets(path)
            if not sheet_names:
                raise ValueError(f"No sheets found in {path.name}")
            for sheet_name in sheet_names:
                if override and len(sheet_names) == 1:
                    base = sanitize_table_name(override)
                elif override and len(sheet_names) > 1:
                    base = sanitize_table_name(f"{override}_{sheet_name}")
                elif len(sheet_names) > 1:
                    base = sanitize_table_name(f"{path.stem}_{sheet_name}")
                else:
                    base = sanitize_table_name(path.stem)
                name = _allocate_table_name(base, used_names, existing, if_exists=if_exists)
                row_count, columns = client.ingest_xlsx_sheet(
                    path, name, sheet=sheet_name, if_exists="replace"
                )
                client.register_table(
                    name,
                    source_file=str(path),
                    sheet_name=sheet_name,
                    original_columns=columns,
                    row_count=row_count,
                )
                existing.add(name)
                tables.append(
                    TableInfo(
                        name=name,
                        sheet_name=sheet_name,
                        row_count=row_count,
                        columns=columns,
                        source_file=str(path),
                    )
                )
            continue

        # Legacy .xls — pandas path (size already guarded)
        sheets = read_spreadsheet(path)
        for sheet_name, df in sheets.items():
            if override and len(sheets) == 1:
                base = sanitize_table_name(override)
            elif override and len(sheets) > 1:
                base = sanitize_table_name(f"{override}_{sheet_name}")
            elif len(sheets) > 1:
                base = sanitize_table_name(f"{path.stem}_{sheet_name}")
            else:
                base = sanitize_table_name(path.stem)

            name = _allocate_table_name(base, used_names, existing, if_exists=if_exists)
            original_columns = [str(c) for c in df.columns]
            if len(df) > limits.max_materialize_rows:
                raise ValueError(
                    f"Sheet '{sheet_name}' has {len(df):,} rows over SAND_MAX_MATERIALIZE_ROWS="
                    f"{limits.max_materialize_rows:,}. Convert to CSV/XLSX/Parquet for native ingest."
                )
            row_count = client.write_dataframe(df, name, if_exists="replace")
            client.register_table(
                name,
                source_file=str(path),
                sheet_name=sheet_name,
                original_columns=original_columns,
                row_count=row_count,
            )
            existing.add(name)
            tables.append(
                TableInfo(
                    name=name,
                    sheet_name=sheet_name,
                    row_count=row_count,
                    columns=original_columns,
                    source_file=str(path),
                )
            )

    result = IngestResult(
        dataset_id=dataset_id,
        db_path=Path(client.path),
        tables=tables,
        source_files=source_files,
    )
    # Pooled clients stay open for reuse
    if owns_client and client.owns_connection:
        client.close()
    return result
