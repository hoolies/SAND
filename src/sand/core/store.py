"""Dataset registry helpers for the API and Jupyter surfaces."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sand.core.config import Settings, get_settings, sanitize_dataset_id
from sand.db.duckdb_client import DuckDBClient
from sand.db.pool import DatabaseLockedError, close_client, get_client


@dataclass
class DatasetInfo:
    id: str
    db_path: Path
    tables: list[str]


@dataclass
class OrphanSqliteFile:
    path: Path
    stem: str


class DatasetStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.ensure_data_dir()

    def list_datasets(self) -> list[DatasetInfo]:
        items: list[DatasetInfo] = []
        for path in sorted(self.settings.data_dir.glob("*.duckdb")):
            ds_id = path.stem
            try:
                client = get_client(path, read_only=True)
                try:
                    tables = client.table_names()
                finally:
                    if client.owns_connection:
                        client.close()
                items.append(DatasetInfo(id=ds_id, db_path=path, tables=tables))
            except DatabaseLockedError:
                items.append(DatasetInfo(id=ds_id, db_path=path, tables=["(locked)"]))
            except Exception:
                items.append(DatasetInfo(id=ds_id, db_path=path, tables=["(unavailable)"]))
        return items

    def list_orphan_sqlite(self) -> list[OrphanSqliteFile]:
        """Legacy SQLite files left over before the DuckDB migration."""
        return [
            OrphanSqliteFile(path=p, stem=p.stem)
            for p in sorted(self.settings.data_dir.glob("*.db"))
            if p.is_file()
        ]

    def delete_orphan_sqlite(self, stem: str) -> Path:
        safe = Path(stem).name
        if safe != stem or "/" in stem or "\\" in stem or stem in {".", ".."}:
            raise ValueError(f"Invalid orphan stem: {stem!r}")
        path = self.settings.data_dir / f"{safe}.db"
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Orphan SQLite file not found: {safe}.db")
        path.unlink()
        return path

    def get_path(self, dataset_id: str) -> Path:
        path = self.settings.db_path(dataset_id)
        if not path.exists():
            raise FileNotFoundError(f"Dataset not found: {dataset_id}")
        return path

    def open(self, dataset_id: str, *, read_only: bool = True) -> DuckDBClient:
        return get_client(self.get_path(dataset_id), read_only=read_only)

    def exists(self, dataset_id: str) -> bool:
        return self.settings.db_path(dataset_id).exists()

    def delete(self, dataset_id: str) -> None:
        from sand.core.chat_store import chat_sidecar_path

        path = self.get_path(dataset_id)
        close_client(path)
        path.unlink()
        for side in (path.with_suffix(path.suffix + ".wal"), path.with_suffix(".duckdb.wal")):
            if side.exists():
                side.unlink(missing_ok=True)
        chat_sidecar_path(dataset_id, self.settings).unlink(missing_ok=True)

    def duplicate(self, dataset_id: str, new_id: str) -> Path:
        from sand.core.chat_store import chat_sidecar_path
        from sand.core.limits import check_data_dir_budget

        src = self.get_path(dataset_id)
        safe = sanitize_dataset_id(new_id)
        dest = self.settings.db_path(safe)
        if dest.exists():
            raise FileExistsError(f"Dataset already exists: {safe}")
        check_data_dir_budget(additional_bytes=src.stat().st_size, settings=self.settings)
        # Checkpoint then copy without evicting the pooled writer
        client = get_client(src, read_only=False)
        client.checkpoint()
        shutil.copy2(src, dest)
        wal = Path(str(src) + ".wal")
        if wal.exists():
            shutil.copy2(wal, Path(str(dest) + ".wal"))
        src_chat = chat_sidecar_path(dataset_id, self.settings)
        if src_chat.exists():
            shutil.copy2(src_chat, chat_sidecar_path(safe, self.settings))
        return dest

    def export_bytes(self, dataset_id: str) -> bytes:
        """Return a consistent on-disk snapshot without dropping the pool connection."""
        path = self.get_path(dataset_id)
        client = get_client(path, read_only=False)
        client.checkpoint()
        return path.read_bytes()

    def import_duckdb(self, src: Path, dataset_id: str) -> Path:
        """Register an existing ``.duckdb`` file as a dataset (copy into data dir)."""
        from sand.core.limits import check_data_dir_budget, check_file_size, limits_from_settings

        src = Path(src)
        if not src.exists():
            raise FileNotFoundError(f"File not found: {src}")
        if src.suffix.lower() != ".duckdb":
            raise ValueError("Import expects a .duckdb file")
        limits = limits_from_settings(self.settings)
        check_file_size(src, max_bytes=limits.max_ingest_bytes, label=src.name)
        safe = sanitize_dataset_id(dataset_id)
        dest = self.settings.db_path(safe)
        if dest.exists():
            raise FileExistsError(f"Dataset already exists: {safe}")
        check_data_dir_budget(additional_bytes=src.stat().st_size, settings=self.settings)
        # Validate readable DuckDB before committing the copy
        probe = DuckDBClient(src, read_only=True, owns_connection=True)
        try:
            _ = probe.table_names()
        finally:
            probe.close()
        shutil.copy2(src, dest)
        return dest

    def rename(self, dataset_id: str, new_id: str) -> Path:
        """Rename a dataset id (moves .duckdb, .wal, and chat sidecar)."""
        from sand.core.chat_store import chat_sidecar_path

        old = sanitize_dataset_id(dataset_id)
        src = self.get_path(old)
        safe = sanitize_dataset_id(new_id)
        if safe == old:
            return src
        dest = self.settings.db_path(safe)
        if dest.exists():
            raise FileExistsError(f"Dataset already exists: {safe}")
        client = get_client(src, read_only=False)
        client.checkpoint()
        close_client(src)
        shutil.move(str(src), str(dest))
        wal = Path(str(src) + ".wal")
        if wal.exists():
            shutil.move(str(wal), str(Path(str(dest) + ".wal")))
        src_chat = chat_sidecar_path(old, self.settings)
        if src_chat.exists():
            shutil.move(str(src_chat), str(chat_sidecar_path(safe, self.settings)))
        return dest
