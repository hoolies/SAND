"""Database client protocol."""

from __future__ import annotations

from typing import Any, Protocol

import pandas as pd


class DatabaseClient(Protocol):
    path: Any

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> None: ...

    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[tuple[Any, ...]]: ...

    def to_dataframe(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> pd.DataFrame: ...

    def schema(self) -> dict[str, list[dict[str, str]]]: ...

    def table_names(self) -> list[str]: ...

    def write_dataframe(self, df: pd.DataFrame, table_name: str, if_exists: str = "replace") -> int: ...

    def close(self) -> None: ...
