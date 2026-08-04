"""Common analytical queries that do not require an LLM."""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd

from sand.db.duckdb_client import DuckDBClient

AggFunc = Literal["sum", "avg", "count", "min", "max"]

_AGG_SQL = {
    "sum": "SUM",
    "avg": "AVG",
    "count": "COUNT",
    "min": "MIN",
    "max": "MAX",
}


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _require_columns(allowed: set[str], *names: str | None, label: str = "column") -> None:
    for name in names:
        if name is None:
            continue
        if name not in allowed:
            raise ValueError(
                f"Unknown {label}: {name!r}. Allowed: {', '.join(sorted(allowed)) or '(none)'}"
            )


class CommonQueries:
    def __init__(self, client: DuckDBClient):
        self.client = client

    def _table_columns(self, table: str) -> set[str]:
        schema = self.client.schema().get(table)
        if schema is None:
            raise ValueError(f"Unknown table: {table}")
        return {c["name"] for c in schema}

    def profile(self, table: str) -> pd.DataFrame:
        schema = self.client.schema().get(table)
        if schema is None:
            raise ValueError(f"Unknown table: {table}")

        rows: list[dict[str, Any]] = []
        total = self.client.fetchall(f"SELECT COUNT(*) FROM {_qi(table)}")[0][0]
        for col in schema:
            cname = col["name"]
            q = f"""
                SELECT
                    COUNT(*) - COUNT({_qi(cname)}) AS nulls,
                    COUNT(DISTINCT {_qi(cname)}) AS distinct_count,
                    MIN({_qi(cname)}) AS min_value,
                    MAX({_qi(cname)}) AS max_value
                FROM {_qi(table)}
            """
            nulls, distinct_count, min_v, max_v = self.client.fetchall(q)[0]
            rows.append(
                {
                    "column": cname,
                    "type": col["type"],
                    "nulls": nulls,
                    "null_pct": round(100.0 * nulls / total, 2) if total else 0.0,
                    "distinct": distinct_count,
                    "min": min_v,
                    "max": max_v,
                    "rows": total,
                }
            )
        return pd.DataFrame(rows)

    def filter_rows(
        self,
        table: str,
        *,
        filters: list[dict[str, Any] | Any] | None = None,
        order_by: str | None = None,
        ascending: bool = True,
        limit: int = 100,
        where: str | None = None,
        **extra: Any,
    ) -> pd.DataFrame:
        """Filter rows using structured predicates only (no raw SQL ``where``).

        Example::

            q.filter_rows("sales", filters=[
                {"column": "region", "op": "eq", "value": "East"},
                {"column": "amount", "op": "gte", "value": 100},
            ])
        """
        from sand.core.limits import ResourceLimitError, limits_from_settings

        if where is not None or "where" in extra:
            raise ValueError(
                "Raw SQL 'where' is not allowed. Pass structured filters="
                "[{column, op, value}, ...] instead "
                "(ops: eq, ne, gt, gte, lt, lte, like, ilike, in, not_in, is_null, is_not_null, between)."
            )
        if extra:
            unknown = ", ".join(sorted(extra))
            raise ValueError(f"Unknown filter_rows params: {unknown}")

        max_n = limits_from_settings().max_offline_ask_rows
        if int(limit) > max_n:
            raise ResourceLimitError(
                f"filter limit {limit:,} exceeds SAND_MAX_OFFLINE_ASK_ROWS={max_n:,}"
            )

        schema = self.client.schema().get(table)
        if schema is None:
            raise ValueError(f"Unknown table: {table}")
        allowed = {c["name"] for c in schema}

        from sand.queries.predicates import compile_predicates

        clause, params = compile_predicates(list(filters or []), allowed_columns=allowed)
        sql = f"SELECT * FROM {_qi(table)}"
        if clause:
            sql += f" WHERE {clause}"
        if order_by:
            if order_by not in allowed:
                raise ValueError(f"Unknown order_by column: {order_by!r}")
            direction = "ASC" if ascending else "DESC"
            sql += f" ORDER BY {_qi(order_by)} {direction}"
        sql += f" LIMIT {int(limit)}"
        return self.client.to_dataframe(sql, params or None)

    def groupby(
        self,
        table: str,
        *,
        group_by: list[str],
        metric: str,
        agg: AggFunc = "sum",
        limit: int = 100,
    ) -> pd.DataFrame:
        from sand.core.limits import ResourceLimitError, limits_from_settings

        if agg not in _AGG_SQL:
            raise ValueError(f"Unsupported agg: {agg}")
        max_n = limits_from_settings().max_offline_ask_rows
        if int(limit) > max_n:
            raise ResourceLimitError(
                f"groupby limit {limit:,} exceeds SAND_MAX_OFFLINE_ASK_ROWS={max_n:,}"
            )
        allowed = self._table_columns(table)
        _require_columns(allowed, *group_by, label="group_by column")
        _require_columns(allowed, metric, label="metric column")
        groups = ", ".join(_qi(g) for g in group_by)
        sql = f"""
            SELECT {groups}, {_AGG_SQL[agg]}({_qi(metric)}) AS {agg}_{metric}
            FROM {_qi(table)}
            GROUP BY {groups}
            ORDER BY {agg}_{metric} DESC
            LIMIT {int(limit)}
        """
        return self.client.to_dataframe(sql)

    def top_n(
        self,
        table: str,
        *,
        column: str,
        n: int = 10,
        ascending: bool = False,
    ) -> pd.DataFrame:
        from sand.core.limits import ResourceLimitError, limits_from_settings

        max_n = limits_from_settings().max_offline_ask_rows
        if int(n) > max_n:
            raise ResourceLimitError(
                f"top_n n={n:,} exceeds SAND_MAX_OFFLINE_ASK_ROWS={max_n:,}"
            )
        allowed = self._table_columns(table)
        _require_columns(allowed, column)
        direction = "ASC" if ascending else "DESC"
        sql = f"SELECT * FROM {_qi(table)} ORDER BY {_qi(column)} {direction} LIMIT {int(n)}"
        return self.client.to_dataframe(sql)

    def missing_report(self, table: str) -> pd.DataFrame:
        profile = self.profile(table)
        return profile.loc[profile["nulls"] > 0, ["column", "nulls", "null_pct", "rows"]].reset_index(drop=True)

    def time_series(
        self,
        table: str,
        *,
        date_column: str,
        metric: str,
        agg: AggFunc = "sum",
        bucket: Literal["day", "month", "year"] = "month",
    ) -> pd.DataFrame:
        if agg not in _AGG_SQL:
            raise ValueError(f"Unsupported agg: {agg}")
        allowed = self._table_columns(table)
        _require_columns(allowed, date_column, label="date_column")
        _require_columns(allowed, metric, label="metric column")
        trunc = {
            "day": f"CAST(date_trunc('day', TRY_CAST({_qi(date_column)} AS TIMESTAMP)) AS DATE)",
            "month": f"strftime(CAST(date_trunc('month', TRY_CAST({_qi(date_column)} AS TIMESTAMP)) AS DATE), '%Y-%m')",
            "year": f"strftime(CAST(date_trunc('year', TRY_CAST({_qi(date_column)} AS TIMESTAMP)) AS DATE), '%Y')",
        }[bucket]
        sql = f"""
            SELECT {trunc} AS period, {_AGG_SQL[agg]}({_qi(metric)}) AS value
            FROM {_qi(table)}
            WHERE {_qi(date_column)} IS NOT NULL
            GROUP BY period
            ORDER BY period
        """
        return self.client.to_dataframe(sql)

    def join_tables(
        self,
        left: str,
        right: str,
        *,
        on: str | list[str] | None = None,
        how: Literal["inner", "left", "right", "full"] = "inner",
        left_on: str | list[str] | None = None,
        right_on: str | list[str] | None = None,
        select: list[str] | None = None,
        as_table: str | None = None,
        limit: int | None = 100,
    ) -> pd.DataFrame:
        from sand.queries.joins import JoinKey, JoinSpec, execute_join

        if on is None and left_on is None:
            left_cols = {c["name"] for c in self.client.schema().get(left, [])}
            right_cols = {c["name"] for c in self.client.schema().get(right, [])}
            shared = sorted(left_cols & right_cols)
            if not shared:
                raise ValueError(f"No shared key between {left} and {right}; pass on= or left_on=/right_on=")
            on = [shared[0]]

        keys: list[str | JoinKey] = []
        if left_on is not None or right_on is not None:
            left_keys = [left_on] if isinstance(left_on, str) else list(left_on or [])
            right_keys = [right_on] if isinstance(right_on, str) else list(right_on or [])
            if len(left_keys) != len(right_keys) or not left_keys:
                raise ValueError("left_on and right_on must be same non-zero length")
            keys = [
                JoinKey(left=left_key, right=right_key)
                for left_key, right_key in zip(left_keys, right_keys, strict=True)
            ]
        elif isinstance(on, str):
            keys = [on]
        else:
            keys = list(on or [])

        spec = JoinSpec(
            left=left,
            right=right,
            on=keys,
            how=how,
            select=select,
            as_table=as_table,
            limit=limit,
        )
        df, _sql = execute_join(self.client, spec)
        return df
