"""Track in-flight DuckDB clients so the UI can interrupt long queries."""

from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sand.db.duckdb_client import DuckDBClient

_lock = threading.Lock()
_by_id: dict[str, DuckDBClient] = {}
_by_dataset: dict[str, set[str]] = {}


def register(client: DuckDBClient, dataset_id: str, query_id: str | None = None) -> str:
    qid = (query_id or "").strip() or str(uuid.uuid4())
    with _lock:
        _by_id[qid] = client
        _by_dataset.setdefault(dataset_id, set()).add(qid)
    return qid


def unregister(query_id: str) -> None:
    with _lock:
        _by_id.pop(query_id, None)
        for ds, ids in list(_by_dataset.items()):
            ids.discard(query_id)
            if not ids:
                _by_dataset.pop(ds, None)


def interrupt(*, query_id: str | None = None, dataset_id: str | None = None) -> int:
    """Interrupt one query or every tracked query for a dataset. Returns count interrupted."""
    targets: list[DuckDBClient] = []
    with _lock:
        if query_id:
            client = _by_id.get(query_id)
            if client is not None:
                targets.append(client)
        elif dataset_id:
            for qid in list(_by_dataset.get(dataset_id, ())):
                client = _by_id.get(qid)
                if client is not None:
                    targets.append(client)
    count = 0
    for client in targets:
        try:
            client.interrupt()
            count += 1
        except Exception:
            pass
    return count
