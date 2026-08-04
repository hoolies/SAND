"""Process-level DuckDB connection cache with clear lock errors."""

from __future__ import annotations

import threading
from pathlib import Path

import duckdb

from sand.db.duckdb_client import DuckDBClient

_lock = threading.RLock()
_clients: dict[str, DuckDBClient] = {}


class DatabaseLockedError(RuntimeError):
    """Raised when DuckDB refuses the file because another process holds it."""


def _key(path: Path) -> str:
    return str(path.resolve())


def get_client(path: Path, *, read_only: bool = True) -> DuckDBClient:
    """Return a cached write client, or an ephemeral read-only client.

    Defaults to **read-only**. Pass ``read_only=False`` for writes (ingest,
    materialize, recipes, …).

    Read-only opens avoid taking a write lock when this process has not already
    opened the dataset for writing. If a write client is already pooled, it is
    reused for in-process reads.
    """
    path = Path(path)
    k = _key(path)

    with _lock:
        existing = _clients.get(k)
        if existing is not None:
            return existing

        if read_only:
            if not path.exists():
                raise FileNotFoundError(f"Dataset file not found: {path}")
            try:
                return DuckDBClient(path, read_only=True, owns_connection=True)
            except duckdb.IOException as exc:
                raise _as_lock_error(path, exc) from exc

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            client = DuckDBClient(path, read_only=False, owns_connection=False)
        except duckdb.IOException as exc:
            raise _as_lock_error(path, exc) from exc
        except Exception as exc:
            if "lock" in str(exc).lower():
                raise _as_lock_error(path, exc) from exc
            raise
        _clients[k] = client
        return client


def _as_lock_error(path: Path, exc: BaseException) -> DatabaseLockedError:
    return DatabaseLockedError(
        f"Dataset file is locked (another SAND process may be using it): {path}. Detail: {exc}"
    )


def close_client(path: Path) -> None:
    k = _key(Path(path))
    with _lock:
        client = _clients.pop(k, None)
    if client is not None:
        client.owns_connection = True
        try:
            client.close()
        except Exception:
            pass


def close_all() -> None:
    with _lock:
        items = list(_clients.items())
        _clients.clear()
    for _, client in items:
        client.owns_connection = True
        try:
            client.close()
        except Exception:
            pass


def checkpoint_all() -> int:
    """Best-effort CHECKPOINT on every pooled write client. Returns count succeeded."""
    with _lock:
        clients = list(_clients.values())
    count = 0
    for client in clients:
        try:
            client.checkpoint()
            count += 1
        except Exception:
            pass
    return count
