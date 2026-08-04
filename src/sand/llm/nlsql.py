"""Natural-language to SQL with read-only guardrails and preview-first evaluation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sand.charts.planner import plan_chart
from sand.charts.plotly_renderer import render_bundle
from sand.charts.specs import ChartSpec, ChartType
from sand.core.chat_store import ChatTurn, append_chat, list_chat
from sand.core.sql_scan import find_limit_value, sql_for_token_scan, strip_sql_comments
from sand.db.duckdb_client import DuckDBClient
from sand.llm.openai_compat import LLMNotConfiguredError, OpenAICompatClient

# DDL/DML + DuckDB session/side-effect statements (REPLACE omitted: clashes with replace())
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|TRUNCATE|"
    r"COPY|INSTALL|LOAD|UNLOAD|EXPORT|IMPORT|CALL|EXECUTE|EXEC|SET|RESET|"
    r"BEGIN|COMMIT|ROLLBACK|MERGE|GRANT|REVOKE|CHECKPOINT|FORCE|"
    r"PREPARE|DEALLOCATE"
    r")\b",
    re.IGNORECASE,
)

_FORBIDDEN_FUNCS = re.compile(
    r"\b("
    r"read_csv|read_csv_auto|read_json|read_json_auto|read_parquet|read_blob|read_text|"
    r"read_xlsx|excel_scan|glob|iceberg_scan|delta_scan|sqlite_scan|"
    r"postgres_scan|mysql_scan|query_table|query|"
    r"write_csv|write_parquet|copy_database|"
    r"getenv|env|current_setting"
    r")\s*\(",
    re.IGNORECASE,
)

_FROM_EXTERNAL = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:[a-z_][\w.]*\s*\(\s*)?'",
    re.IGNORECASE,
)

# FROM/JOIN relation: optional schema.table
_RELATION = re.compile(
    r"""\b(?:FROM|JOIN)\s+(?:ONLY\s+)?
        (?:(?P<schema>"[^"]+"|[A-Za-z_][\w$]*)\s*\.\s*)?
        (?P<table>"[^"]+"|[A-Za-z_][\w$]*)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CTE_NAME = re.compile(
    r"""(?:(?:WITH)\s+|,)\s*(?P<name>"[^"]+"|[A-Za-z_][\w$]*)\s+AS\s*\(""",
    re.IGNORECASE | re.VERBOSE,
)

_FORBIDDEN_SCHEMAS = {
    "information_schema",
    "pg_catalog",
    "duckdb_catalog",
    "main_schema",  # unused; keep short
}

_FORBIDDEN_TABLE_PREFIXES = ("duckdb_", "pragma_", "sqlite_")

EVAL_LIMIT = 10


@dataclass
class ChatResult:
    summary: str
    sql: str
    sql_preview: str
    chart: dict[str, Any]
    preview: list[dict[str, Any]]
    row_count: int
    evaluated_limit: int = EVAL_LIMIT
    is_preview: bool = True
    full_row_count: int | None = None
    chart_sample_rows: int | None = None
    chart_capped: bool = False


def _unquote(ident: str) -> str:
    ident = ident.strip()
    if len(ident) >= 2 and ident[0] == '"' and ident[-1] == '"':
        return ident[1:-1].replace('""', '"')
    return ident


def _cte_names(scanned: str) -> set[str]:
    return {_unquote(m.group("name")) for m in _CTE_NAME.finditer(scanned)}


def assert_tables_allowed(sql: str, allowed_tables: set[str] | list[str]) -> None:
    """Ensure FROM/JOIN targets are ingested tables or CTEs (not catalogs/system)."""
    scanned = sql_for_token_scan(sql)
    allowed = set(allowed_tables)
    ctes = _cte_names(scanned)
    for match in _RELATION.finditer(scanned):
        # Skip table functions: FROM foo(
        end = match.end()
        rest = scanned[end : end + 8].lstrip()
        if rest.startswith("("):
            continue
        schema = match.group("schema")
        table = _unquote(match.group("table"))
        if schema:
            schema_name = _unquote(schema).lower()
            if schema_name in _FORBIDDEN_SCHEMAS or schema_name.startswith("duckdb"):
                raise ValueError(f"Schema '{schema_name}' is not allowed; query ingested tables only")
            # schema.table — treat as qualified; only main/unqualified ingested names allowed
            if schema_name not in {"main", ""}:
                raise ValueError(f"Schema '{schema_name}' is not allowed; query ingested tables only")
        if table in ctes:
            continue
        if table.lower().startswith(_FORBIDDEN_TABLE_PREFIXES) or table.lower() in {
            "information_schema",
        }:
            raise ValueError(f"Relation '{table}' is not allowed; query ingested tables only")
        if table not in allowed:
            raise ValueError(
                f"Unknown or disallowed table '{table}'. Allowed: {', '.join(sorted(allowed)) or '(none)'}"
            )


def assert_readonly_sql(sql: str, *, allowed_tables: set[str] | list[str] | None = None) -> str:
    """Allow only SELECT/WITH queries over in-database relations."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("Empty SQL")
    if ";" in cleaned:
        raise ValueError("Multiple SQL statements are not allowed")

    no_comments = strip_sql_comments(cleaned)
    if not re.match(r"^\s*(WITH\b|SELECT\b)", no_comments, re.IGNORECASE):
        raise ValueError("SQL must start with SELECT or WITH")
    if _FROM_EXTERNAL.search(no_comments):
        raise ValueError("External FROM/JOIN paths/URLs are not allowed; query ingested tables only")

    scanned = sql_for_token_scan(cleaned)
    if _FORBIDDEN_KEYWORDS.search(scanned):
        raise ValueError("Only read-only SELECT/WITH queries against dataset tables are allowed")
    if _FORBIDDEN_FUNCS.search(scanned):
        raise ValueError(
            "Filesystem/remote table functions (read_csv, read_parquet, glob, …) are not allowed"
        )
    if allowed_tables is not None:
        assert_tables_allowed(cleaned, allowed_tables)
    return cleaned


def with_eval_limit(sql: str, limit: int = EVAL_LIMIT) -> str:
    """Force a small LIMIT via an outer wrap (immune to LIMIT inside string literals)."""
    cleaned = sql.strip().rstrip(";").strip()
    return f"SELECT * FROM ({cleaned}) AS _sand_eval LIMIT {int(limit)}"


def _schema_prompt(schema: dict[str, list[dict[str, str]]]) -> str:
    lines = []
    for table, cols in schema.items():
        col_str = ", ".join(f"{c['name']} ({c['type']})" for c in cols)
        lines.append(f"- {table}: {col_str}")
    return "\n".join(lines) if lines else "(no tables)"


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


class NLSQLChat:
    def __init__(
        self,
        client: DuckDBClient,
        *,
        dataset_id: str,
        llm: OpenAICompatClient | None = None,
    ):
        self.client = client
        self.dataset_id = dataset_id
        self.llm = llm or OpenAICompatClient()

    def ask(
        self,
        message: str,
        *,
        chart_type: ChartType | None = None,
        chart_override: ChartSpec | None = None,
        history: list[ChatTurn] | None = None,
        run_full: bool = False,
        sql_override: str | None = None,
        persist: bool = True,
        bypass_chart_cap: bool = False,
    ) -> ChatResult:
        schema = self.client.schema()
        allowed = set(self.client.table_names())
        history = (
            history
            if history is not None
            else list_chat(self.dataset_id, limit=20, migrate_from=self.client)
        )

        if sql_override:
            sql = assert_readonly_sql(sql_override, allowed_tables=allowed)
            summary = "Running confirmed SQL."
            preferred = chart_type
        else:
            if not self.llm.is_configured:
                raise LLMNotConfiguredError(
                    "No LLM configured. Use Offline asks (Profile / Missing / Top-N / Group-by / "
                    "Time series), or set SAND_LLM_API_KEY / SAND_LLM_BASE_URL."
                )
            system = (
                "You convert natural language questions into DuckDB SELECT queries.\n"
                "Return ONLY valid JSON with keys: sql (string), summary (string), "
                "preferred_chart (one of bar|line|scatter|pie|heatmap|table or null).\n"
                "Rules:\n"
                "- Use only the provided tables/columns.\n"
                "- Read-only SELECT/WITH queries only against those tables.\n"
                "- Never use COPY, INSTALL/LOAD, read_csv/read_parquet/glob, or FROM 'path'/'url'.\n"
                "- Never query information_schema or duckdb_* catalogs.\n"
                "- Prefer aggregations suitable for charts when asking for trends or comparisons.\n"
                "- Use DuckDB SQL (strftime, date_trunc, etc. are fine).\n"
                "- Do NOT include LIMIT unless the user asks for a specific count; the system "
                f"always evaluates with LIMIT {EVAL_LIMIT} first.\n"
                "- Use prior conversation for follow-ups (e.g. chart type changes).\n"
            )
            history_lines = []
            for turn in history[-12:]:
                history_lines.append(f"{turn.role}: {turn.content}" + (f" [sql={turn.sql}]" if turn.sql else ""))
            user = (
                f"Schema:\n{_schema_prompt(schema)}\n\n"
                f"Conversation:\n" + ("\n".join(history_lines) if history_lines else "(none)") + "\n\n"
                f"Question: {message}\n"
            )
            raw = self.llm.complete(system, user)
            try:
                sql, summary, preferred = self._parse_llm_sql(raw, allowed=allowed, chart_type=chart_type)
            except ValueError as guard_exc:
                bad = ""
                try:
                    bad = str(_extract_json(raw).get("sql", ""))
                except Exception:
                    bad = raw[:500]
                sql, summary, preferred = self._repair_sql(
                    system=system,
                    schema_prompt=_schema_prompt(schema),
                    question=message,
                    bad_sql=bad,
                    error=str(guard_exc),
                    allowed=allowed,
                    chart_type=chart_type,
                )

        from sand.core.limits import estimate_sql_rows, guard_result_rows, limits_from_settings

        limits = limits_from_settings()
        sql_preview = with_eval_limit(sql, EVAL_LIMIT)
        default_cap = max(EVAL_LIMIT, int(limits.chart_sample_rows))
        # Saved views may opt into loading up to max_result_rows for charts
        chart_cap = int(limits.max_result_rows) if bypass_chart_cap else default_cap
        full_row_count: int | None = None

        if run_full:
            guard_result_rows(self.client, sql, max_rows=limits.max_result_rows, action="full chat query")
            try:
                full_row_count = estimate_sql_rows(self.client, sql, probe_limit=limits.max_result_rows)
            except Exception:
                full_row_count = find_limit_value(sql)
            run_sql = with_eval_limit(sql, min(chart_cap, limits.max_result_rows))
        else:
            run_sql = sql_preview
            try:
                full_row_count = estimate_sql_rows(self.client, sql, probe_limit=100_000)
                if full_row_count is not None and full_row_count > 100_000:
                    full_row_count = 100_000
            except Exception:
                full_row_count = find_limit_value(sql)

        try:
            df = self.client.to_dataframe(run_sql)
        except Exception as exec_exc:
            if sql_override or not self.llm.is_configured:
                raise
            sql, summary, preferred = self._repair_sql(
                system=(
                    "You convert natural language questions into DuckDB SELECT queries.\n"
                    "Return ONLY valid JSON with keys: sql, summary, preferred_chart.\n"
                    "Fix the failed SQL. Read-only SELECT/WITH only; use only provided tables.\n"
                ),
                schema_prompt=_schema_prompt(schema),
                question=message,
                bad_sql=sql,
                error=str(exec_exc),
                allowed=allowed,
                chart_type=chart_type,
            )
            sql_preview = with_eval_limit(sql, EVAL_LIMIT)
            run_sql = sql if run_full else sql_preview
            if run_full:
                run_sql = with_eval_limit(sql, min(chart_cap, limits.max_result_rows))
                guard_result_rows(self.client, sql, max_rows=limits.max_result_rows, action="full chat query")
            df = self.client.to_dataframe(run_sql)

        if chart_override is not None:
            spec = chart_override
        else:
            spec = plan_chart(df, preferred=preferred, title=summary[:80])

        bundle = render_bundle(df, spec)
        shown = bundle["row_count"]
        full_n = full_row_count if full_row_count is not None else shown
        chart_capped = bool(run_full and full_n is not None and shown < int(full_n))
        result = ChatResult(
            summary=summary,
            sql=sql,
            sql_preview=sql_preview,
            chart=bundle,
            preview=bundle["preview"],
            row_count=shown,
            evaluated_limit=EVAL_LIMIT if not run_full else min(chart_cap, shown),
            is_preview=not run_full,
            full_row_count=full_n,
            chart_sample_rows=chart_cap if run_full else None,
            chart_capped=chart_capped,
        )

        if persist and not sql_override:
            append_chat(self.dataset_id, ChatTurn(role="user", content=message))
            append_chat(
                self.dataset_id,
                ChatTurn(
                    role="assistant",
                    content=summary,
                    sql=sql,
                    meta={
                        "is_preview": result.is_preview,
                        "row_count": result.row_count,
                        "full_row_count": result.full_row_count,
                    },
                ),
            )
        return result

    def _parse_llm_sql(
        self,
        raw: str,
        *,
        allowed: set[str],
        chart_type: ChartType | None,
    ) -> tuple[str, str, Any]:
        try:
            payload = _extract_json(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM did not return valid JSON: {raw[:400]}") from exc
        sql = assert_readonly_sql(str(payload.get("sql", "")), allowed_tables=allowed)
        summary = str(payload.get("summary") or "Query complete.")
        preferred = chart_type or payload.get("preferred_chart") or None
        if preferred == "null":
            preferred = None
        return sql, summary, preferred

    def _repair_sql(
        self,
        *,
        system: str,
        schema_prompt: str,
        question: str,
        bad_sql: str,
        error: str,
        allowed: set[str],
        chart_type: ChartType | None,
    ) -> tuple[str, str, Any]:
        repair_user = (
            f"Schema:\n{schema_prompt}\n\n"
            f"Question: {question}\n\n"
            f"Previous SQL failed validation/execution:\n{bad_sql}\n\n"
            f"Error: {error}\n\n"
            "Return corrected JSON only."
        )
        raw = self.llm.complete(system, repair_user)
        return self._parse_llm_sql(raw, allowed=allowed, chart_type=chart_type)
