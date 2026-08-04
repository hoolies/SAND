"""Path helpers for spreadsheet ingest (CSV / XLSX / Parquet)."""

from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".parquet"}


def list_xlsx_sheets(path: str | Path) -> list[str]:
    """List sheet names without loading cell data."""
    from openpyxl import load_workbook

    wb = load_workbook(Path(path), read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()
