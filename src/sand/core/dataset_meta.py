"""Dataset-local persistence: join recipes (chat history lives in chat_store sidecar)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from sand.db.duckdb_client import METADATA_TABLE, DuckDBClient, sanitize_table_name
from sand.queries.joins import JoinPlan, JoinSpec

RECIPES_TABLE = "_sand_join_recipes"
VIEWS_TABLE = "_sand_views"
# Legacy name kept so drop/rename guards still block old in-DB chat tables
CHAT_TABLE = "_sand_chat_history"


class JoinRecipe(BaseModel):
    name: str
    spec: JoinSpec | None = None
    plan: JoinPlan | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, name: str, spec_json: str, created_at: Any) -> JoinRecipe:
        raw: Any
        try:
            raw = json.loads(spec_json)
        except json.JSONDecodeError:
            raw = JoinSpec.model_validate_json(spec_json).model_dump()
        created = None if created_at is None else str(created_at)
        if isinstance(raw, dict) and "steps" in raw:
            return cls(name=name, plan=JoinPlan.model_validate(raw), created_at=created)
        return cls(name=name, spec=JoinSpec.model_validate(raw), created_at=created)


class SavedView(BaseModel):
    name: str
    sql: str
    chart_type: str | None = None
    cache_enabled: bool = False
    allow_over_cap: bool = False
    has_cache: bool = False
    created_at: str | None = None
    cached_at: str | None = None

    @classmethod
    def from_row(
        cls,
        name: str,
        sql: str,
        chart_type: Any,
        cache_enabled: Any,
        allow_over_cap: Any,
        cache_json: Any,
        created_at: Any,
        cached_at: Any,
    ) -> SavedView:
        return cls(
            name=name,
            sql=sql,
            chart_type=None if chart_type in (None, "") else str(chart_type),
            cache_enabled=bool(cache_enabled),
            allow_over_cap=bool(allow_over_cap),
            has_cache=bool(cache_json),
            created_at=None if created_at is None else str(created_at),
            cached_at=None if cached_at is None else str(cached_at),
        )


def ensure_aux_tables(client: DuckDBClient) -> None:
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {RECIPES_TABLE} (
            name VARCHAR PRIMARY KEY,
            spec_json VARCHAR NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {VIEWS_TABLE} (
            name VARCHAR PRIMARY KEY,
            sql_text VARCHAR NOT NULL,
            chart_type VARCHAR,
            cache_enabled BOOLEAN DEFAULT FALSE,
            allow_over_cap BOOLEAN DEFAULT FALSE,
            cache_json VARCHAR,
            created_at TIMESTAMP DEFAULT now(),
            cached_at TIMESTAMP
        )
        """
    )


def save_recipe(
    client: DuckDBClient,
    name: str,
    spec: JoinSpec | None = None,
    plan: JoinPlan | None = None,
) -> JoinRecipe:
    if spec is None and plan is None:
        raise ValueError("Provide join spec or plan")
    if spec is not None and plan is not None:
        raise ValueError("Provide only one of join spec or plan")
    ensure_aux_tables(client)
    recipe_name = re_sub_name(name)
    now = datetime.now(UTC).isoformat()
    payload = plan.model_dump_json() if plan is not None else spec.model_dump_json()  # type: ignore[union-attr]
    client.execute(
        f"""
        INSERT INTO {RECIPES_TABLE} (name, spec_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET spec_json=excluded.spec_json, created_at=excluded.created_at
        """,
        (recipe_name, payload, now),
    )
    return JoinRecipe(name=recipe_name, spec=spec, plan=plan, created_at=now)


def list_recipes(client: DuckDBClient) -> list[JoinRecipe]:
    ensure_aux_tables(client)
    rows = client.fetchall(f"SELECT name, spec_json, created_at FROM {RECIPES_TABLE} ORDER BY created_at DESC")
    out: list[JoinRecipe] = []
    for name, spec_json, created_at in rows:
        out.append(JoinRecipe.from_row(name, spec_json, created_at))
    return out


def get_recipe(client: DuckDBClient, name: str) -> JoinRecipe | None:
    ensure_aux_tables(client)
    rows = client.fetchall(
        f"SELECT name, spec_json, created_at FROM {RECIPES_TABLE} WHERE name = ?",
        (name,),
    )
    if not rows:
        return None
    name, spec_json, created_at = rows[0]
    return JoinRecipe.from_row(name, spec_json, created_at)


def delete_recipe(client: DuckDBClient, name: str) -> bool:
    ensure_aux_tables(client)
    before = client.fetchall(f"SELECT COUNT(*) FROM {RECIPES_TABLE} WHERE name = ?", (name,))[0][0]
    client.execute(f"DELETE FROM {RECIPES_TABLE} WHERE name = ?", (name,))
    return before > 0


def save_view(
    client: DuckDBClient,
    name: str,
    sql: str,
    *,
    chart_type: str | None = None,
    cache_enabled: bool = False,
    allow_over_cap: bool = False,
) -> SavedView:
    ensure_aux_tables(client)
    view_name = re_sub_name(name)
    now = datetime.now(UTC).isoformat()
    existing = get_view(client, view_name)
    cache_json = None
    cached_at = None
    if existing and existing.has_cache and cache_enabled:
        rows = client.fetchall(
            f"SELECT cache_json, cached_at FROM {VIEWS_TABLE} WHERE name = ?",
            (view_name,),
        )
        if rows:
            cache_json, cached_at = rows[0]
            cached_at = None if cached_at is None else str(cached_at)
    created = now if existing is None else (existing.created_at or now)
    client.execute(
        f"""
        INSERT INTO {VIEWS_TABLE}
            (name, sql_text, chart_type, cache_enabled, allow_over_cap, cache_json, created_at, cached_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            sql_text=excluded.sql_text,
            chart_type=excluded.chart_type,
            cache_enabled=excluded.cache_enabled,
            allow_over_cap=excluded.allow_over_cap,
            cache_json=excluded.cache_json,
            created_at=excluded.created_at,
            cached_at=excluded.cached_at
        """,
        (
            view_name,
            sql.strip(),
            chart_type,
            cache_enabled,
            allow_over_cap,
            None if not cache_enabled else cache_json,
            created,
            None if not cache_enabled else cached_at,
        ),
    )
    return get_view(client, view_name) or SavedView(
        name=view_name,
        sql=sql.strip(),
        chart_type=chart_type,
        cache_enabled=cache_enabled,
        allow_over_cap=allow_over_cap,
        created_at=created,
    )


def list_views(client: DuckDBClient) -> list[SavedView]:
    ensure_aux_tables(client)
    rows = client.fetchall(
        f"""
        SELECT name, sql_text, chart_type, cache_enabled, allow_over_cap, cache_json, created_at, cached_at
        FROM {VIEWS_TABLE}
        ORDER BY created_at DESC
        """
    )
    return [SavedView.from_row(*row) for row in rows]


def get_view(client: DuckDBClient, name: str) -> SavedView | None:
    ensure_aux_tables(client)
    rows = client.fetchall(
        f"""
        SELECT name, sql_text, chart_type, cache_enabled, allow_over_cap, cache_json, created_at, cached_at
        FROM {VIEWS_TABLE}
        WHERE name = ?
        """,
        (name,),
    )
    if not rows:
        return None
    return SavedView.from_row(*rows[0])


def get_view_cache(client: DuckDBClient, name: str) -> dict[str, Any] | None:
    ensure_aux_tables(client)
    rows = client.fetchall(f"SELECT cache_json FROM {VIEWS_TABLE} WHERE name = ?", (name,))
    if not rows or not rows[0][0]:
        return None
    try:
        data = json.loads(rows[0][0])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def set_view_cache(client: DuckDBClient, name: str, payload: dict[str, Any]) -> None:
    ensure_aux_tables(client)
    now = datetime.now(UTC).isoformat()
    client.execute(
        f"UPDATE {VIEWS_TABLE} SET cache_json = ?, cached_at = ? WHERE name = ?",
        (json.dumps(payload), now, name),
    )


def clear_view_cache(client: DuckDBClient, name: str) -> bool:
    ensure_aux_tables(client)
    before = client.fetchall(f"SELECT COUNT(*) FROM {VIEWS_TABLE} WHERE name = ?", (name,))[0][0]
    if before <= 0:
        return False
    client.execute(
        f"UPDATE {VIEWS_TABLE} SET cache_json = NULL, cached_at = NULL WHERE name = ?",
        (name,),
    )
    return True


def delete_view(client: DuckDBClient, name: str) -> bool:
    ensure_aux_tables(client)
    before = client.fetchall(f"SELECT COUNT(*) FROM {VIEWS_TABLE} WHERE name = ?", (name,))[0][0]
    client.execute(f"DELETE FROM {VIEWS_TABLE} WHERE name = ?", (name,))
    return before > 0


def re_sub_name(name: str) -> str:
    import re

    cleaned = re.sub(r"[^0-9a-zA-Z _-]+", "", name.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return (cleaned or "recipe")[:64]


def drop_table(client: DuckDBClient, table: str) -> None:
    if table in {METADATA_TABLE, RECIPES_TABLE, VIEWS_TABLE, CHAT_TABLE} or table.startswith("_sand"):
        raise ValueError("Cannot drop internal SAND tables")
    if table not in client.table_names():
        raise ValueError(f"Unknown table: {table}")
    client.execute(f'DROP TABLE IF EXISTS "{table.replace(chr(34), chr(34)+chr(34))}"')
    client.execute(f"DELETE FROM {METADATA_TABLE} WHERE table_name = ?", (table,))


def rename_table(client: DuckDBClient, old: str, new: str) -> str:
    if old not in client.table_names():
        raise ValueError(f"Unknown table: {old}")
    safe = sanitize_table_name(new)
    if safe in client.table_names():
        raise ValueError(f"Table already exists: {safe}")
    client.execute(f'ALTER TABLE "{old.replace(chr(34), chr(34)+chr(34))}" RENAME TO "{safe}"')
    client.execute(
        f"UPDATE {METADATA_TABLE} SET table_name = ? WHERE table_name = ?",
        (safe, old),
    )
    return safe
