"""Explicit join specifications for multi-table datasets."""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from sand.db.duckdb_client import DuckDBClient, sanitize_table_name

JoinHow = Literal["inner", "left", "right", "full"]

_JOIN_SQL = {
    "inner": "INNER JOIN",
    "left": "LEFT JOIN",
    "right": "RIGHT JOIN",
    "full": "FULL OUTER JOIN",
}


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class JoinKey(BaseModel):
    """Map a left-table column to a right-table column."""

    left: str
    right: str


class JoinSpec(BaseModel):
    """How to join two tables.

    ``on`` accepts:
    - shared column names: ``["customer_id"]``
    - renamed keys: ``["order_cust_id=customer_id"]`` or ``[{"left": "...", "right": "..."}]``
    """

    left: str
    right: str
    on: list[str | JoinKey] = Field(min_length=1)
    how: JoinHow = "inner"
    select: list[str] | None = None
    as_table: str | None = None
    limit: int | None = Field(default=None, ge=1)

    @field_validator("on", mode="before")
    @classmethod
    def _coerce_on(cls, value: Any) -> list[Any]:
        if isinstance(value, str):
            return [value]
        return value

    def key_pairs(self) -> list[JoinKey]:
        pairs: list[JoinKey] = []
        for item in self.on:
            if isinstance(item, JoinKey):
                pairs.append(item)
            elif isinstance(item, dict):
                pairs.append(JoinKey.model_validate(item))
            elif isinstance(item, str):
                if "=" in item:
                    left, right = item.split("=", 1)
                    pairs.append(JoinKey(left=left.strip(), right=right.strip()))
                else:
                    pairs.append(JoinKey(left=item.strip(), right=item.strip()))
            else:
                raise ValueError(f"Invalid join key: {item!r}")
        return pairs


class JoinPlan(BaseModel):
    """Chain of joins starting from the first step's left table."""

    steps: list[JoinSpec] = Field(min_length=1)
    as_table: str | None = None
    limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _align_chain(self) -> JoinPlan:
        # Later steps' left defaults to previous right only when left matches; we keep explicit lefts.
        return self


def build_join_sql(client: DuckDBClient, spec: JoinSpec, *, alias_left: str = "t0", alias_right: str = "t1") -> str:
    schema = client.schema()
    if spec.left not in schema:
        raise ValueError(f"Unknown left table: {spec.left}")
    if spec.right not in schema:
        raise ValueError(f"Unknown right table: {spec.right}")

    left_cols = {c["name"] for c in schema[spec.left]}
    right_cols = {c["name"] for c in schema[spec.right]}
    pairs = spec.key_pairs()
    for pair in pairs:
        if pair.left not in left_cols:
            raise ValueError(f"Column {pair.left!r} not in left table {spec.left}")
        if pair.right not in right_cols:
            raise ValueError(f"Column {pair.right!r} not in right table {spec.right}")

    if spec.how not in _JOIN_SQL:
        raise ValueError(f"Unsupported join type: {spec.how}")

    join_sql = _JOIN_SQL[spec.how]
    on_clause = " AND ".join(
        f"{alias_left}.{_qi(p.left)} = {alias_right}.{_qi(p.right)}" for p in pairs
    )

    if spec.select:
        select_sql = ", ".join(_resolve_select(col, alias_left, alias_right) for col in spec.select)
    else:
        # Qualify all columns; rename collisions on the right side.
        left_select = [f"{alias_left}.{_qi(c)} AS {_qi(c)}" for c in sorted(left_cols)]
        right_select = []
        for c in sorted(right_cols):
            if c in left_cols:
                right_select.append(f"{alias_right}.{_qi(c)} AS {_qi(f'{spec.right}__{c}')}")
            else:
                right_select.append(f"{alias_right}.{_qi(c)} AS {_qi(c)}")
        select_sql = ", ".join(left_select + right_select)

    sql = (
        f"SELECT {select_sql} FROM {_qi(spec.left)} AS {alias_left} "
        f"{join_sql} {_qi(spec.right)} AS {alias_right} ON {on_clause}"
    )
    if spec.limit is not None:
        sql += f" LIMIT {int(spec.limit)}"
    return sql


def _resolve_select(col: str, alias_left: str, alias_right: str) -> str:
    """Accept ``table.column``, ``left.column``, ``t0.column``, or bare column (left-preferring)."""
    if "." in col:
        table, name = col.split(".", 1)
        table = table.strip()
        name = name.strip()
        if table in {"left", "t0", "l"}:
            return f"{alias_left}.{_qi(name)} AS {_qi(name)}"
        if table in {"right", "t1", "r"}:
            return f"{alias_right}.{_qi(name)} AS {_qi(name)}"
        return f"{_qi(table)}.{_qi(name)}"
    return f"{alias_left}.{_qi(col)} AS {_qi(col)}"


def execute_join(client: DuckDBClient, spec: JoinSpec) -> tuple[pd.DataFrame, str]:
    """Run a join in DuckDB. Failures raise — no silent pandas fallback."""
    from sand.core.limits import guard_result_rows, limits_from_settings

    sql = build_join_sql(client, spec)
    limits = limits_from_settings()

    # Materialize in-engine when requested (avoids double scan via Python)
    if spec.as_table:
        name = sanitize_table_name(spec.as_table)
        sql_no_limit = build_join_sql(client, spec.model_copy(update={"limit": None}))
        n = guard_result_rows(
            client, sql_no_limit, max_rows=limits.max_materialize_rows, action="join materialize"
        )
        client.create_table_as(name, sql_no_limit)
        client.register_table(
            name,
            source_file="join",
            sheet_name="joined",
            original_columns=[c["name"] for c in client.schema().get(name, [])],
            row_count=n,
        )
        preview_sql = f'SELECT * FROM "{name}"' + (f" LIMIT {int(spec.limit)}" if spec.limit else f" LIMIT {min(500, limits.max_result_rows)}")
        guard_result_rows(client, preview_sql, max_rows=limits.max_result_rows, action="join preview")
        return client.to_dataframe(preview_sql), sql_no_limit

    guard_result_rows(client, sql, max_rows=limits.max_result_rows, action="join")
    return client.to_dataframe(sql), sql


def execute_join_plan(client: DuckDBClient, plan: JoinPlan) -> tuple[pd.DataFrame, str]:
    """Execute a chain of joins using in-DB temp tables (no pandas fallback)."""
    if len(plan.steps) == 1:
        step = plan.steps[0]
        if plan.as_table and not step.as_table:
            step = step.model_copy(update={"as_table": plan.as_table, "limit": plan.limit or step.limit})
        elif plan.limit and not step.limit:
            step = step.model_copy(update={"limit": plan.limit})
        return execute_join(client, step)

    from sand.core.limits import guard_result_rows, limits_from_settings

    limits = limits_from_settings()
    sqls: list[str] = []
    temp_name = "_sand_join_tmp_0"
    first = plan.steps[0].model_copy(update={"as_table": temp_name, "limit": None})
    _, sql = execute_join(client, first)
    sqls.append(sql)

    for i, step in enumerate(plan.steps[1:], start=1):
        next_temp = f"_sand_join_tmp_{i}"
        rewritten = step.model_copy(update={"left": temp_name, "as_table": next_temp, "limit": None})
        _, sql = execute_join(client, rewritten)
        sqls.append(sql)
        try:
            client.execute(f'DROP TABLE IF EXISTS "{temp_name}"')
        except Exception:
            pass
        temp_name = next_temp

    final_sql = " ;\n".join(sqls)
    as_table = plan.as_table or plan.steps[-1].as_table
    if as_table:
        name = sanitize_table_name(as_table)
        client.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "{temp_name}"')
        n = int(client.fetchall(f'SELECT COUNT(*) FROM "{name}"')[0][0])
        client.register_table(
            name,
            source_file="join",
            sheet_name="joined",
            original_columns=[c["name"] for c in client.schema().get(name, [])],
            row_count=n,
        )
        out_table = name
    else:
        out_table = temp_name

    limit = plan.limit or 500
    preview_sql = f'SELECT * FROM "{out_table}" LIMIT {int(limit)}'
    guard_result_rows(client, preview_sql, max_rows=limits.max_result_rows, action="join plan preview")
    df = client.to_dataframe(preview_sql)

    for i in range(len(plan.steps)):
        try:
            client.execute(f'DROP TABLE IF EXISTS "_sand_join_tmp_{i}"')
        except Exception:
            pass
    return df, final_sql
