"""Best-practice chart recommendations from result shape."""

from __future__ import annotations

import pandas as pd

from sand.charts.specs import ChartSpec, ChartType


def _is_datetime_like(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if series.dtype == object:
        sample = series.dropna().head(20)
        if sample.empty:
            return False
        try:
            pd.to_datetime(sample, errors="raise")
            return True
        except (ValueError, TypeError):
            return False
    return False


def _numeric_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _categorical_cols(df: pd.DataFrame) -> list[str]:
    nums = set(_numeric_cols(df))
    return [c for c in df.columns if c not in nums]


def plan_chart(df: pd.DataFrame, *, preferred: ChartType | None = None, title: str | None = None) -> ChartSpec:
    """Recommend a chart type and encodings from a result DataFrame."""
    if df.empty or len(df.columns) == 0:
        return ChartSpec(chart_type="table", title=title or "Empty result", reason="No data to plot")

    if preferred == "table" or len(df) > 500:
        return ChartSpec(
            chart_type="table",
            title=title or "Result table",
            reason="Too many rows or explicit table request; show a table instead",
        )

    nums = _numeric_cols(df)
    cats = _categorical_cols(df)
    time_cols = [c for c in df.columns if _is_datetime_like(df[c])]

    if preferred:
        x = time_cols[0] if preferred == "line" and time_cols else (cats[0] if cats else (df.columns[0] if len(df.columns) else None))
        y = nums[0] if nums else (df.columns[1] if len(df.columns) > 1 else None)
        orientation = "h" if preferred == "bar" and cats and df[cats[0]].nunique() > 8 else "v"
        return ChartSpec(
            chart_type=preferred,
            title=title or f"{preferred.title()} chart",
            x=x,
            y=y,
            orientation=orientation,
            reason="User-specified chart type",
        )

    # Time + numeric → line
    if time_cols and nums:
        return ChartSpec(
            chart_type="line",
            title=title or f"{nums[0]} over time",
            x=time_cols[0],
            y=nums[0],
            reason="Time-like column with a numeric metric → line chart",
        )

    # Two numerics → scatter
    if len(nums) >= 2 and len(cats) == 0:
        return ChartSpec(
            chart_type="scatter",
            title=title or f"{nums[0]} vs {nums[1]}",
            x=nums[0],
            y=nums[1],
            reason="Two numeric columns → scatter plot",
        )

    # Category + metric → bar (horizontal if many categories)
    if cats and nums:
        nunique = df[cats[0]].nunique(dropna=True)
        if nunique <= 6 and preferred is None and _looks_like_share(df, nums[0]):
            return ChartSpec(
                chart_type="pie",
                title=title or f"{nums[0]} by {cats[0]}",
                x=cats[0],
                y=nums[0],
                reason="Few categories with share-like values → pie (≤6 slices)",
            )
        orientation = "h" if nunique > 8 else "v"
        return ChartSpec(
            chart_type="bar",
            title=title or f"{nums[0]} by {cats[0]}",
            x=cats[0],
            y=nums[0],
            orientation=orientation,
            reason="Category + metric → bar chart" + (" (horizontal for many categories)" if orientation == "h" else ""),
        )

    # Wide numeric matrix → heatmap
    if len(nums) >= 3 and len(df) >= 3 and len(cats) <= 1:
        return ChartSpec(
            chart_type="heatmap",
            title=title or "Heatmap",
            x=cats[0] if cats else df.columns[0],
            reason="Wide numeric matrix → heatmap",
        )

    return ChartSpec(
        chart_type="table",
        title=title or "Result table",
        reason="Could not infer a clear chart encoding; showing a table",
    )


def _looks_like_share(df: pd.DataFrame, col: str) -> bool:
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty or s.min() < 0 or len(s) > 6:
        return False
    total = float(s.sum())
    if total == 0:
        return False
    # Values look like fractions or percentages of a whole
    if 0.9 <= total <= 1.1:
        return True
    if 90 <= total <= 110 and s.max() <= 100:
        return True
    return False
