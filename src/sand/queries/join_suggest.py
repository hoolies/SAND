"""Join key suggestions, cardinality estimates, and fan-out warnings."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from sand.db.duckdb_client import DuckDBClient
from sand.queries.joins import JoinSpec, build_join_sql, execute_join

_ID_HINT = re.compile(r"(^id$|_id$|id$|code$|key$|sku$|uuid$)", re.IGNORECASE)


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


class KeySuggestion(BaseModel):
    left: str
    right: str
    score: float
    reason: str


class JoinEstimate(BaseModel):
    left_rows: int
    right_rows: int
    left_distinct: int
    right_distinct: int
    estimated_rows: int | None = None
    matched_left: int | None = None
    matched_right: int | None = None
    multiplicity: str = Field(description="one_to_one | one_to_many | many_to_one | many_to_many | unknown")
    warning: str | None = None
    sql_preview: str | None = None


class JoinSuggestResponse(BaseModel):
    suggestions: list[KeySuggestion]
    estimate: JoinEstimate | None = None


def suggest_join_keys(client: DuckDBClient, left: str, right: str, *, limit: int = 8) -> list[KeySuggestion]:
    schema = client.schema()
    if left not in schema or right not in schema:
        raise ValueError("Unknown left or right table")

    left_cols = [c["name"] for c in schema[left]]
    right_cols = [c["name"] for c in schema[right]]
    suggestions: list[KeySuggestion] = []

    right_norm = {_normalize(c): c for c in right_cols}
    for lc in left_cols:
        ln = _normalize(lc)
        if ln in right_norm:
            suggestions.append(
                KeySuggestion(left=lc, right=right_norm[ln], score=1.0, reason="exact/normalized name match")
            )
            continue

        # Strong heuristic: foo_id ↔ id / fooId ↔ id
        for rc in right_cols:
            rn = _normalize(rc)
            if ln.endswith("id") and rn == "id" and ln != "id":
                suggestions.append(
                    KeySuggestion(left=lc, right=rc, score=0.95, reason="foreign-key style (*_id → id)")
                )
            elif rn.endswith("id") and ln == "id" and rn != "id":
                suggestions.append(
                    KeySuggestion(left=lc, right=rc, score=0.95, reason="foreign-key style (id → *_id)")
                )
            elif ln.endswith(rn) and len(rn) >= 2 and _ID_HINT.search(lc) and _ID_HINT.search(rc):
                suggestions.append(
                    KeySuggestion(left=lc, right=rc, score=0.9, reason="suffix id/key match")
                )

        best = None
        best_score = 0.0
        for rc in right_cols:
            score = SequenceMatcher(None, ln, _normalize(rc)).ratio()
            if _ID_HINT.search(lc) and _ID_HINT.search(rc):
                score = min(1.0, score + 0.2)
            if score > best_score:
                best_score = score
                best = rc
        if best is not None and best_score >= 0.65:
            suggestions.append(
                KeySuggestion(left=lc, right=best, score=round(best_score, 3), reason="fuzzy column-name match")
            )

    suggestions.sort(key=lambda s: (-s.score, s.left, s.right))
    dedup: list[KeySuggestion] = []
    seen: set[tuple[str, str]] = set()
    for s in suggestions:
        key = (s.left, s.right)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(s)
    return dedup[:limit]


def estimate_join(client: DuckDBClient, spec: JoinSpec) -> JoinEstimate:
    pairs = spec.key_pairs()
    left_rows = client.fetchall(f"SELECT COUNT(*) FROM {_qi(spec.left)}")[0][0]
    right_rows = client.fetchall(f"SELECT COUNT(*) FROM {_qi(spec.right)}")[0][0]

    left_expr = ", ".join(_qi(p.left) for p in pairs)
    right_expr = ", ".join(_qi(p.right) for p in pairs)
    left_distinct = client.fetchall(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {left_expr} FROM {_qi(spec.left)})"
    )[0][0]
    right_distinct = client.fetchall(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {right_expr} FROM {_qi(spec.right)})"
    )[0][0]

    # Sample match counts via a limited probe when possible
    matched_left = matched_right = estimated = None
    warning = None
    multiplicity = "unknown"
    sql_preview = None

    try:
        # Build inner-join count without user limit
        probe = JoinSpec(left=spec.left, right=spec.right, on=spec.on, how="inner", limit=None)
        sql_preview = build_join_sql(client, probe)
        estimated = client.fetchall(f"SELECT COUNT(*) FROM ({sql_preview})")[0][0]
        matched_left = client.fetchall(
            f"""
            SELECT COUNT(DISTINCT {", ".join(f"l.{_qi(p.left)}" for p in pairs)})
            FROM {_qi(spec.left)} AS l
            INNER JOIN {_qi(spec.right)} AS r
              ON {" AND ".join(f"l.{_qi(p.left)} = r.{_qi(p.right)}" for p in pairs)}
            """
        )[0][0]
        matched_right = client.fetchall(
            f"""
            SELECT COUNT(DISTINCT {", ".join(f"r.{_qi(p.right)}" for p in pairs)})
            FROM {_qi(spec.left)} AS l
            INNER JOIN {_qi(spec.right)} AS r
              ON {" AND ".join(f"l.{_qi(p.left)} = r.{_qi(p.right)}" for p in pairs)}
            """
        )[0][0]
    except Exception:
        # Fall back to pandas path estimate
        try:
            probe = JoinSpec(left=spec.left, right=spec.right, on=spec.on, how="inner", limit=None)
            df, sql_preview = execute_join(client, probe)
            estimated = len(df)
        except Exception as exc:  # noqa: BLE001
            warning = f"Could not estimate join size: {exc}"

    left_unique = left_distinct == left_rows and left_rows > 0
    right_unique = right_distinct == right_rows and right_rows > 0
    if left_unique and right_unique:
        multiplicity = "one_to_one"
    elif left_unique and not right_unique:
        multiplicity = "one_to_many"
    elif not left_unique and right_unique:
        multiplicity = "many_to_one"
    else:
        multiplicity = "many_to_many"
        if estimated is not None and estimated > max(left_rows, right_rows) * 1.2:
            warning = (
                f"Many-to-many fan-out likely: ~{estimated} result rows from "
                f"{left_rows}×{right_rows} inputs. Check join keys."
            )
        elif warning is None:
            warning = "Join keys are not unique on either side (many-to-many). Result may explode."

    return JoinEstimate(
        left_rows=left_rows,
        right_rows=right_rows,
        left_distinct=left_distinct,
        right_distinct=right_distinct,
        estimated_rows=estimated,
        matched_left=matched_left,
        matched_right=matched_right,
        multiplicity=multiplicity,
        warning=warning,
        sql_preview=sql_preview,
    )
