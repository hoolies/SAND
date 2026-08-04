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


def _relation_columns(client: DuckDBClient, table: str) -> set[str]:
    """Column names for a base or TEMP relation (UI schema() hides temps / ``_sand_*``)."""
    rows = client.fetchall(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        ORDER BY ordinal_position
        """,
        (table,),
    )
    if not rows:
        raise ValueError(f"Unknown table: {table}")
    return {str(r[0]) for r in rows}


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


def build_join_sql_with_cols(
    client: DuckDBClient,
    spec: JoinSpec,
    *,
    left_relation_sql: str | None = None,
    left_columns: set[str] | list[str] | None = None,
    alias_left: str = "t0",
    alias_right: str = "t1",
) -> tuple[str, list[str]]:
    """Build join SQL and the output column names (order matches SELECT)."""
    if left_relation_sql is not None:
        if left_columns is None:
            raise ValueError("left_columns required when left_relation_sql is set")
        left_cols = set(left_columns)
        left_from = f"({left_relation_sql}) AS {alias_left}"
        left_label = spec.left if spec.left not in {"__prev__", ""} else "__prev__"
    else:
        try:
            left_cols = _relation_columns(client, spec.left)
        except ValueError as exc:
            raise ValueError(f"Unknown left table: {spec.left}") from exc
        left_from = f"{_qi(spec.left)} AS {alias_left}"
        left_label = spec.left

    try:
        right_cols = _relation_columns(client, spec.right)
    except ValueError as exc:
        raise ValueError(f"Unknown right table: {spec.right}") from exc

    pairs = spec.key_pairs()
    for pair in pairs:
        if pair.left not in left_cols:
            raise ValueError(f"Column {pair.left!r} not in left table {left_label}")
        if pair.right not in right_cols:
            raise ValueError(f"Column {pair.right!r} not in right table {spec.right}")

    if spec.how not in _JOIN_SQL:
        raise ValueError(f"Unsupported join type: {spec.how}")

    join_sql = _JOIN_SQL[spec.how]
    on_clause = " AND ".join(
        f"{alias_left}.{_qi(p.left)} = {alias_right}.{_qi(p.right)}" for p in pairs
    )

    out_cols: list[str] = []
    if spec.select:
        select_parts: list[str] = []
        for col in spec.select:
            part = _resolve_select(col, alias_left, alias_right)
            select_parts.append(part)
            # best-effort output name from AS clause
            if " AS " in part.upper():
                out_cols.append(part.rsplit(" AS ", 1)[-1].strip().strip('"'))
            else:
                out_cols.append(col.split(".")[-1])
        select_sql = ", ".join(select_parts)
    else:
        left_select = [f"{alias_left}.{_qi(c)} AS {_qi(c)}" for c in sorted(left_cols)]
        out_cols.extend(sorted(left_cols))
        right_select = []
        for c in sorted(right_cols):
            if c in left_cols:
                alias = f"{spec.right}__{c}"
                right_select.append(f"{alias_right}.{_qi(c)} AS {_qi(alias)}")
                out_cols.append(alias)
            else:
                right_select.append(f"{alias_right}.{_qi(c)} AS {_qi(c)}")
                out_cols.append(c)
        select_sql = ", ".join(left_select + right_select)

    sql = (
        f"SELECT {select_sql} FROM {left_from} "
        f"{join_sql} {_qi(spec.right)} AS {alias_right} ON {on_clause}"
    )
    if spec.limit is not None:
        sql += f" LIMIT {int(spec.limit)}"
    return sql, out_cols


def build_join_sql(client: DuckDBClient, spec: JoinSpec, *, alias_left: str = "t0", alias_right: str = "t1") -> str:
    sql, _ = build_join_sql_with_cols(client, spec, alias_left=alias_left, alias_right=alias_right)
    return sql


def build_nested_join_plan_sql(client: DuckDBClient, plan: JoinPlan) -> str:
    """Single nested SELECT for a multi-step plan (no TEMP tables)."""
    if not plan.steps:
        raise ValueError("Join plan has no steps")
    first = plan.steps[0].model_copy(update={"as_table": None, "limit": None})
    sql, cols = build_join_sql_with_cols(client, first)
    for step in plan.steps[1:]:
        rewritten = step.model_copy(update={"left": "__prev__", "as_table": None, "limit": None})
        sql, cols = build_join_sql_with_cols(
            client,
            rewritten,
            left_relation_sql=sql,
            left_columns=cols,
        )
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
    """Execute a chain of joins as nested SQL (no TEMP / no leftover tables)."""
    if len(plan.steps) == 1:
        step = plan.steps[0]
        if plan.as_table and not step.as_table:
            step = step.model_copy(update={"as_table": plan.as_table, "limit": plan.limit or step.limit})
        elif plan.limit and not step.limit:
            step = step.model_copy(update={"limit": plan.limit})
        return execute_join(client, step)

    from sand.core.limits import guard_result_rows, limits_from_settings

    limits = limits_from_settings()
    nested = build_nested_join_plan_sql(client, plan)
    as_table = plan.as_table or plan.steps[-1].as_table

    if as_table:
        name = sanitize_table_name(as_table)
        n = guard_result_rows(
            client, nested, max_rows=limits.max_materialize_rows, action="join plan materialize"
        )
        client.create_table_as(name, nested)
        client.register_table(
            name,
            source_file="join",
            sheet_name="joined",
            original_columns=[c["name"] for c in client.schema().get(name, [])],
            row_count=n,
        )
        limit = plan.limit or min(500, limits.max_result_rows)
        preview_sql = f'SELECT * FROM "{name}" LIMIT {int(limit)}'
        guard_result_rows(client, preview_sql, max_rows=limits.max_result_rows, action="join plan preview")
        return client.to_dataframe(preview_sql), nested

    limit = plan.limit or 500
    preview_sql = f"SELECT * FROM ({nested}) AS _sand_plan LIMIT {int(limit)}"
    guard_result_rows(client, preview_sql, max_rows=limits.max_result_rows, action="join plan preview")
    return client.to_dataframe(preview_sql), nested
