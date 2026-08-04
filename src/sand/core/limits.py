"""Resource limits and preflight checks to avoid OOM."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from sand.core.config import Settings, get_settings
from sand.db.duckdb_client import DuckDBClient
from sand.core.sql_scan import find_limit_value

_SIMPLE_TABLE_RE = re.compile(
    r"""^\s*SELECT\s+\*\s+FROM\s+(?:"([^"]+)"|([A-Za-z_][\w$]*))\s*(?:LIMIT\s+\d+\s*)?;?\s*$""",
    re.IGNORECASE,
)


class ResourceLimitError(ValueError):
    """Raised when an operation would exceed configured memory/row guards."""


class Limits(BaseModel):
    max_ingest_bytes: int = Field(default=200 * 1024 * 1024, description="Max spreadsheet file size")
    max_result_rows: int = Field(default=100_000, description="Max rows returned to Python/API")
    max_export_rows: int = Field(default=500_000, description="Max rows for CSV/XLSX export")
    max_materialize_rows: int = Field(default=2_000_000, description="Max rows for join materialization")
    excel_pandas_max_bytes: int = Field(
        default=50 * 1024 * 1024,
        description="Legacy .xls still uses pandas; refuse larger workbooks",
    )
    max_offline_ask_rows: int = Field(
        default=10_000,
        description="Max n/limit for top_n, groupby, and similar offline asks",
    )
    max_data_dir_bytes: int = Field(
        default=10 * 1024 * 1024 * 1024,
        description="Max total size of SAND data dir (0 disables)",
    )
    query_timeout_seconds: float = Field(default=30.0, description="DuckDB statement timeout")


def limits_from_settings(settings: Settings | None = None) -> Limits:
    s = settings or get_settings()
    return Limits(
        max_ingest_bytes=s.max_ingest_bytes,
        max_result_rows=s.max_result_rows,
        max_export_rows=s.max_export_rows,
        max_materialize_rows=s.max_materialize_rows,
        excel_pandas_max_bytes=s.excel_pandas_max_bytes,
        max_offline_ask_rows=s.max_offline_ask_rows,
        max_data_dir_bytes=s.max_data_dir_bytes,
        query_timeout_seconds=s.query_timeout_seconds,
    )


def check_file_size(path: Path, *, max_bytes: int, label: str = "file") -> int:
    size = path.stat().st_size
    if size > max_bytes:
        raise ResourceLimitError(
            f"{label} is {size:,} bytes, over the limit of {max_bytes:,} bytes. "
            f"Split the file or raise SAND_MAX_INGEST_BYTES."
        )
    return size


def data_dir_usage_bytes(data_dir: Path) -> int:
    if not data_dir.exists():
        return 0
    total = 0
    for path in data_dir.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def check_data_dir_budget(*, additional_bytes: int = 0, settings: Settings | None = None) -> int:
    s = settings or get_settings()
    limits = limits_from_settings(s)
    if limits.max_data_dir_bytes <= 0:
        return data_dir_usage_bytes(s.data_dir)
    used = data_dir_usage_bytes(s.data_dir)
    if used + max(0, additional_bytes) > limits.max_data_dir_bytes:
        raise ResourceLimitError(
            f"Data dir would exceed SAND_MAX_DATA_DIR_BYTES={limits.max_data_dir_bytes:,} "
            f"(currently {used:,} bytes, adding {additional_bytes:,}). "
            f"Delete datasets or raise the limit."
        )
    return used


def estimate_sql_rows(client: DuckDBClient, sql: str, *, probe_limit: int | None = None) -> int:
    """Estimate rows without always doing a full COUNT(*) scan.

    LIMIT detection ignores string literals / comments so ``WHERE x = 'LIMIT 1'``
    cannot bypass the guard.
    """
    cleaned = sql.strip().rstrip(";")
    limit_val = find_limit_value(cleaned)
    if limit_val is not None:
        return limit_val

    simple = _SIMPLE_TABLE_RE.match(cleaned)
    if simple:
        table = simple.group(1) or simple.group(2)
        return int(client.fetchall(f'SELECT COUNT(*) FROM "{table}"')[0][0])

    try:
        if probe_limit is not None:
            probe_sql = (
                f"SELECT COUNT(*) FROM ("
                f"SELECT 1 FROM ({cleaned}) AS _sand_probe LIMIT {int(probe_limit) + 1}"
                f") AS _sand_c"
            )
            return int(client.fetchall(probe_sql)[0][0])
        return int(client.fetchall(f"SELECT COUNT(*) FROM ({cleaned}) AS _sand_count_sub")[0][0])
    except Exception as exc:  # noqa: BLE001
        raise ResourceLimitError(f"Could not estimate result size before loading into memory: {exc}") from exc


def guard_result_rows(client: DuckDBClient, sql: str, *, max_rows: int, action: str = "query") -> int:
    n = estimate_sql_rows(client, sql, probe_limit=max_rows)
    if n > max_rows:
        raise ResourceLimitError(
            f"{action} would return at least {n:,} rows (limit {max_rows:,}). "
            f"Add filters/aggregations, use LIMIT, or raise SAND_MAX_RESULT_ROWS."
        )
    return n


def guard_table_rows(client: DuckDBClient, table: str, *, max_rows: int, action: str = "operation") -> int:
    n = int(client.fetchall(f'SELECT COUNT(*) FROM "{table}"')[0][0])
    if n > max_rows:
        raise ResourceLimitError(
            f"{action} on '{table}' touches {n:,} rows (limit {max_rows:,}). "
            f"Filter first or raise the matching SAND_*_ROWS limit."
        )
    return n
