"""Ingest package."""

from sand.ingest.loader import IngestResult, TableInfo, ingest_file, ingest_files
from sand.ingest.readers import SUPPORTED_EXTENSIONS, read_spreadsheet

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "IngestResult",
    "TableInfo",
    "ingest_file",
    "ingest_files",
    "read_spreadsheet",
]
