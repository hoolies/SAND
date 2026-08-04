"""SQL comment/string stripping and LIMIT helpers shared by guards and NL→SQL."""

from __future__ import annotations

import re

_COMMENT_LINE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_SINGLE_QUOTED = re.compile(r"'(?:''|[^'])*'")
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)


def strip_sql_comments(sql: str) -> str:
    without_blocks = _COMMENT_BLOCK.sub(" ", sql)
    return _COMMENT_LINE.sub(" ", without_blocks)


def strip_sql_strings(sql: str) -> str:
    """Replace string literals so keyword/LIMIT scans ignore values like 'LIMIT 1'."""
    return _SINGLE_QUOTED.sub("''", sql)


def sql_for_token_scan(sql: str) -> str:
    return strip_sql_strings(strip_sql_comments(sql))


def find_limit_value(sql: str) -> int | None:
    """Return LIMIT n from SQL ignoring comments/string literals, else None."""
    match = _LIMIT_RE.search(sql_for_token_scan(sql))
    return int(match.group(1)) if match else None
