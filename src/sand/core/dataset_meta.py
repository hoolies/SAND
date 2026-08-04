"""Dataset-local persistence: join recipes and chat memory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from sand.db.duckdb_client import METADATA_TABLE, DuckDBClient, sanitize_table_name
from sand.queries.joins import JoinSpec

RECIPES_TABLE = "_sand_join_recipes"
CHAT_TABLE = "_sand_chat_history"


class JoinRecipe(BaseModel):
    name: str
    spec: JoinSpec
    created_at: str | None = None

    @classmethod
    def from_row(cls, name: str, spec_json: str, created_at: Any) -> JoinRecipe:
        return cls(
            name=name,
            spec=JoinSpec.model_validate_json(spec_json),
            created_at=None if created_at is None else str(created_at),
        )


class ChatTurn(BaseModel):
    role: str
    content: str
    sql: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


def ensure_aux_tables(client: DuckDBClient) -> None:
    client.execute("CREATE SEQUENCE IF NOT EXISTS _sand_chat_id_seq START 1")
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
        CREATE TABLE IF NOT EXISTS {CHAT_TABLE} (
            id BIGINT PRIMARY KEY DEFAULT nextval('_sand_chat_id_seq'),
            role VARCHAR NOT NULL,
            content VARCHAR NOT NULL,
            sql VARCHAR,
            meta_json VARCHAR,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )


def save_recipe(client: DuckDBClient, name: str, spec: JoinSpec) -> JoinRecipe:
    ensure_aux_tables(client)
    safe = sanitize_table_name(name) if name else "recipe"
    # keep human name mostly; still sanitize lightly
    recipe_name = re_sub_name(name)
    now = datetime.now(timezone.utc).isoformat()
    client.execute(
        f"""
        INSERT INTO {RECIPES_TABLE} (name, spec_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET spec_json=excluded.spec_json, created_at=excluded.created_at
        """,
        (recipe_name, spec.model_dump_json(), now),
    )
    return JoinRecipe(name=recipe_name, spec=spec, created_at=now)


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


def append_chat(client: DuckDBClient, turn: ChatTurn) -> None:
    ensure_aux_tables(client)
    client.execute(
        f"""
        INSERT INTO {CHAT_TABLE} (role, content, sql, meta_json)
        VALUES (?, ?, ?, ?)
        """,
        (turn.role, turn.content, turn.sql, json.dumps(turn.meta)),
    )


def list_chat(client: DuckDBClient, *, limit: int = 50) -> list[ChatTurn]:
    ensure_aux_tables(client)
    rows = client.fetchall(
        f"""
        SELECT role, content, sql, meta_json, created_at
        FROM {CHAT_TABLE}
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    turns = [
        ChatTurn(
            role=r[0],
            content=r[1],
            sql=r[2],
            meta=json.loads(r[3] or "{}"),
            created_at=None if r[4] is None else str(r[4]),
        )
        for r in rows
    ]
    turns.reverse()
    return turns


def clear_chat(client: DuckDBClient) -> None:
    ensure_aux_tables(client)
    client.execute(f"DELETE FROM {CHAT_TABLE}")


def re_sub_name(name: str) -> str:
    import re

    cleaned = re.sub(r"[^0-9a-zA-Z _-]+", "", name.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return (cleaned or "recipe")[:64]


def drop_table(client: DuckDBClient, table: str) -> None:
    if table in {METADATA_TABLE, RECIPES_TABLE, CHAT_TABLE} or table.startswith("_sand"):
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
