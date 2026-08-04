"""Environment-based configuration for SAND."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DATASET_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


def sanitize_dataset_id(value: str) -> str:
    """Normalize a dataset id to a safe single-path segment under the data dir.

    Allows ``[A-Za-z0-9_-]``, max 64 chars, must not be empty / ``.`` / ``..`` /
    contain path separators. Rejects anything that would escape ``data_dir``.
    """
    if value is None:
        raise ValueError("Dataset id is required")
    raw = str(value).strip()
    if not raw:
        raise ValueError("Invalid dataset id: empty")
    if raw in {".", ".."} or "/" in raw or "\\" in raw:
        raise ValueError(f"Invalid dataset id: {value!r}")
    cleaned = re.sub(r"[^0-9A-Za-z_-]+", "_", raw).strip("_")
    if not cleaned or not _DATASET_ID_RE.match(cleaned):
        raise ValueError(f"Invalid dataset id: {value!r}")
    if not re.search(r"[0-9A-Za-z]", cleaned):
        raise ValueError(f"Invalid dataset id: {value!r}")
    return cleaned[:64]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAND_", env_file=".env", extra="ignore")

    data_dir: Path = Path(".sand/data")
    host: str = "127.0.0.1"
    port: int = 8765
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    default_db_name: str = "sand.duckdb"

    # Resource guards (avoid OOM)
    max_ingest_bytes: int = 200 * 1024 * 1024
    max_result_rows: int = 100_000
    max_export_rows: int = 500_000
    max_materialize_rows: int = 2_000_000
    excel_pandas_max_bytes: int = 50 * 1024 * 1024  # legacy .xls only
    max_offline_ask_rows: int = 10_000
    max_data_dir_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GiB; 0 disables
    query_timeout_seconds: float = 30.0
    api_token: str = ""  # optional Bearer / X-SAND-Token for Docker publishes
    allow_insecure_bind: bool = False  # allow 0.0.0.0 without api_token

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    def db_path(self, dataset_id: str | None = None) -> Path:
        self.ensure_data_dir()
        if dataset_id is None:
            path = (self.data_dir / self.default_db_name).resolve()
        else:
            safe = sanitize_dataset_id(dataset_id)
            path = (self.data_dir / f"{safe}.duckdb").resolve()
        data_root = self.data_dir.resolve()
        if path != data_root and data_root not in path.parents:
            raise ValueError(f"Dataset path escapes data dir: {dataset_id!r}")
        return path


def get_settings() -> Settings:
    return Settings()
