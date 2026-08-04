"""JSONL sidecar chat history — keeps dataset DuckDB opens read-only for asks."""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from sand.core.config import Settings, get_settings, sanitize_dataset_id
from sand.db.duckdb_client import DuckDBClient

LEGACY_CHAT_TABLE = "_sand_chat_history"


class ChatTurn(BaseModel):
    role: str
    content: str
    sql: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


def chat_sidecar_path(dataset_id: str, settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    safe = sanitize_dataset_id(dataset_id)
    return s.data_dir / f"{safe}.chat.jsonl"


def append_chat(dataset_id: str, turn: ChatTurn, *, settings: Settings | None = None) -> None:
    path = chat_sidecar_path(dataset_id, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = turn.model_copy(
        update={"created_at": turn.created_at or datetime.now(UTC).isoformat()}
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(payload.model_dump_json() + "\n")


def list_chat(
    dataset_id: str,
    *,
    limit: int = 50,
    settings: Settings | None = None,
    migrate_from: DuckDBClient | None = None,
) -> list[ChatTurn]:
    path = chat_sidecar_path(dataset_id, settings)
    if (not path.exists() or path.stat().st_size == 0) and migrate_from is not None:
        migrate_legacy_chat(dataset_id, migrate_from, settings=settings)

    if not path.exists():
        return []

    lines: deque[str] = deque(maxlen=max(1, int(limit)))
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return [ChatTurn.model_validate_json(item) for item in lines]


def clear_chat(dataset_id: str, *, settings: Settings | None = None) -> None:
    chat_sidecar_path(dataset_id, settings).unlink(missing_ok=True)


def migrate_legacy_chat(
    dataset_id: str,
    client: DuckDBClient,
    *,
    settings: Settings | None = None,
) -> int:
    """Copy `_sand_chat_history` from an older DuckDB file into the JSONL sidecar once."""
    path = chat_sidecar_path(dataset_id, settings)
    if path.exists() and path.stat().st_size > 0:
        return 0
    try:
        rows = client.fetchall(
            f"""
            SELECT role, content, sql, meta_json, created_at
            FROM {LEGACY_CHAT_TABLE}
            ORDER BY id ASC
            """
        )
    except Exception:
        return 0
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for role, content, sql, meta_json, created_at in rows:
            turn = ChatTurn(
                role=str(role),
                content=str(content),
                sql=sql,
                meta=json.loads(meta_json or "{}"),
                created_at=None if created_at is None else str(created_at),
            )
            fh.write(turn.model_dump_json() + "\n")
    return len(rows)
