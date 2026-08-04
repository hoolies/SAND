"""Pydantic-validated column typing for spreadsheet ingest."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator

SandType = Literal["integer", "float", "boolean", "datetime", "date", "string", "unknown"]


class ColumnTypeSpec(BaseModel):
    name: str
    inferred: SandType = "unknown"
    override: SandType | None = None
    nullable: bool = True
    sample_values: list[Any] = Field(default_factory=list)
    null_count: int = 0
    distinct_count: int = 0

    @property
    def effective(self) -> SandType:
        return self.override or self.inferred


class TableTypePlan(BaseModel):
    table_name: str
    columns: list[ColumnTypeSpec]

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for col in self.columns:
            if col.name not in out.columns:
                raise ValueError(f"Missing column {col.name}")
            out[col.name] = coerce_series(out[col.name], col.effective)
        return out


class IngestTypeReport(BaseModel):
    dataset_id: str
    tables: list[TableTypePlan]


def infer_series_type(series: pd.Series) -> SandType:
    non_null = series.dropna()
    if non_null.empty:
        return "unknown"
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    sample = non_null.astype(str).head(50)
    # boolean-like
    lowered = {v.strip().lower() for v in sample}
    if lowered and lowered <= {"true", "false", "0", "1", "yes", "no", "y", "n"}:
        return "boolean"
    # numeric
    as_num = pd.to_numeric(sample.str.replace(",", "", regex=False), errors="coerce")
    if as_num.notna().mean() >= 0.9:
        if (as_num.dropna() % 1 == 0).all():
            return "integer"
        return "float"
    # datetime / date
    as_dt = pd.to_datetime(sample, errors="coerce", utc=False, format="mixed")
    if as_dt.notna().mean() >= 0.9:
        times = as_dt.dropna()
        if len(times) and all(getattr(t, "hour", 0) == 0 and getattr(t, "minute", 0) == 0 for t in times):
            return "date"
        return "datetime"
    return "string"


def coerce_series(series: pd.Series, sand_type: SandType) -> pd.Series:
    if sand_type in {"string", "unknown"}:
        return series.astype("string")
    if sand_type == "integer":
        return pd.to_numeric(series, errors="coerce").astype("Int64")
    if sand_type == "float":
        return pd.to_numeric(series, errors="coerce")
    if sand_type == "boolean":
        mapping = {
            "true": True,
            "false": False,
            "yes": True,
            "no": False,
            "y": True,
            "n": False,
            "1": True,
            "0": False,
        }
        if pd.api.types.is_bool_dtype(series):
            return series
        return series.map(lambda v: mapping.get(str(v).strip().lower(), pd.NA) if pd.notna(v) else pd.NA)
    if sand_type == "datetime":
        return pd.to_datetime(series, errors="coerce")
    if sand_type == "date":
        dt = pd.to_datetime(series, errors="coerce")
        return dt.dt.date
    return series


def build_type_plan(table_name: str, df: pd.DataFrame) -> TableTypePlan:
    cols: list[ColumnTypeSpec] = []
    for name in df.columns:
        s = df[name]
        inferred = infer_series_type(s)
        sample = [None if pd.isna(v) else (v.isoformat() if isinstance(v, (datetime, date)) else v) for v in s.head(5).tolist()]
        cols.append(
            ColumnTypeSpec(
                name=str(name),
                inferred=inferred,
                nullable=bool(s.isna().any()),
                sample_values=sample,
                null_count=int(s.isna().sum()),
                distinct_count=int(s.nunique(dropna=True)),
            )
        )
    return TableTypePlan(table_name=table_name, columns=cols)


class TypeOverride(BaseModel):
    name: str
    type: SandType

    @field_validator("type")
    @classmethod
    def _valid(cls, v: SandType) -> SandType:
        return v


class ApplyTypesRequest(BaseModel):
    columns: list[TypeOverride]


_DUCK_TYPES = {
    "integer": "BIGINT",
    "float": "DOUBLE",
    "boolean": "BOOLEAN",
    "datetime": "TIMESTAMP",
    "date": "DATE",
    "string": "VARCHAR",
    "unknown": "VARCHAR",
}


def apply_types_sql(client: Any, table: str, plan: TableTypePlan) -> int:
    """Rewrite a table in DuckDB using TRY_CAST — no full pandas round-trip."""
    from sand.core.limits import guard_table_rows, limits_from_settings
    from sand.db.duckdb_client import sanitize_table_name

    limits = limits_from_settings()
    guard_table_rows(client, table, max_rows=limits.max_materialize_rows, action="type apply")

    safe = sanitize_table_name(table)
    tmp = f"_sand_retype_{safe}"
    parts: list[str] = []
    for col in plan.columns:
        q = '"' + col.name.replace('"', '""') + '"'
        duck = _DUCK_TYPES[col.effective]
        if col.effective in {"string", "unknown"}:
            parts.append(f"CAST({q} AS VARCHAR) AS {q}")
        else:
            parts.append(f"TRY_CAST({q} AS {duck}) AS {q}")
    select_list = ", ".join(parts)
    client.execute(f'CREATE OR REPLACE TABLE "{tmp}" AS SELECT {select_list} FROM "{safe}"')
    client.execute(f'DROP TABLE "{safe}"')
    client.execute(f'ALTER TABLE "{tmp}" RENAME TO "{safe}"')
    n = int(client.fetchall(f'SELECT COUNT(*) FROM "{safe}"')[0][0])
    client.register_table(
        safe,
        source_file="retyped",
        sheet_name=safe,
        original_columns=[c.name for c in plan.columns],
        row_count=n,
    )
    return n
