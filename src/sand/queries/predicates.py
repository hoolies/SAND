"""Safe structured filter predicates (no raw SQL WHERE injection)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FilterOp = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "like",
    "ilike",
    "in",
    "not_in",
    "is_null",
    "is_not_null",
    "between",
]

_OPS_NEED_VALUE = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "like",
    "ilike",
    "in",
    "not_in",
    "between",
}

_SQL_OP = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
    "ilike": "ILIKE",
}


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class FilterPredicate(BaseModel):
    """One column comparison. Values are bound as parameters — never interpolated as SQL."""

    column: str = Field(min_length=1)
    op: FilterOp = "eq"
    value: Any = None

    @model_validator(mode="after")
    def _check_value(self) -> FilterPredicate:
        if self.op in {"is_null", "is_not_null"}:
            return self
        if self.op in _OPS_NEED_VALUE and self.value is None:
            raise ValueError(f"op={self.op!r} requires a value")
        if self.op in {"in", "not_in"}:
            if not isinstance(self.value, (list, tuple)) or not self.value:
                raise ValueError(f"op={self.op!r} requires a non-empty list value")
        if self.op == "between":
            if not isinstance(self.value, (list, tuple)) or len(self.value) != 2:
                raise ValueError("op='between' requires value=[low, high]")
        return self


def compile_predicates(
    predicates: list[FilterPredicate] | list[dict[str, Any]],
    *,
    allowed_columns: set[str] | None = None,
) -> tuple[str, list[Any]]:
    """Compile AND-combined predicates into a WHERE clause + bound params.

    Returns (sql_fragment_without_WHERE, params).
    """
    if not predicates:
        return "", []

    parts: list[str] = []
    params: list[Any] = []

    for raw in predicates:
        pred = raw if isinstance(raw, FilterPredicate) else FilterPredicate.model_validate(raw)
        col = pred.column
        if allowed_columns is not None and col not in allowed_columns:
            raise ValueError(f"Unknown or disallowed column: {col!r}")
        qcol = _qi(col)

        if pred.op == "is_null":
            parts.append(f"{qcol} IS NULL")
        elif pred.op == "is_not_null":
            parts.append(f"{qcol} IS NOT NULL")
        elif pred.op == "between":
            parts.append(f"{qcol} BETWEEN ? AND ?")
            params.extend([pred.value[0], pred.value[1]])
        elif pred.op in {"in", "not_in"}:
            placeholders = ", ".join("?" for _ in pred.value)
            keyword = "IN" if pred.op == "in" else "NOT IN"
            parts.append(f"{qcol} {keyword} ({placeholders})")
            params.extend(list(pred.value))
        else:
            parts.append(f"{qcol} {_SQL_OP[pred.op]} ?")
            params.append(pred.value)

    return " AND ".join(parts), params
