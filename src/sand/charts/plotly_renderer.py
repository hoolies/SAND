"""Plotly chart renderer."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder

from sand.charts.specs import ChartSpec


def render_figure(df: pd.DataFrame, spec: ChartSpec) -> go.Figure:
    if spec.chart_type == "table" or df.empty:
        fig = go.Figure(
            data=[
                go.Table(
                    header=dict(values=list(df.columns), fill_color="#1f2937", font=dict(color="white")),
                    cells=dict(values=[df[c].tolist() for c in df.columns], fill_color="#111827", font=dict(color="#e5e7eb")),
                )
            ]
        )
        fig.update_layout(title=spec.title)
        return fig

    if spec.chart_type == "line":
        fig = px.line(df, x=spec.x, y=spec.y, color=spec.color, title=spec.title)
    elif spec.chart_type == "bar":
        fig = px.bar(
            df,
            x=spec.x if spec.orientation == "v" else spec.y,
            y=spec.y if spec.orientation == "v" else spec.x,
            color=spec.color,
            title=spec.title,
            orientation=spec.orientation,
        )
    elif spec.chart_type == "scatter":
        fig = px.scatter(df, x=spec.x, y=spec.y, color=spec.color, title=spec.title)
    elif spec.chart_type == "pie":
        fig = px.pie(df, names=spec.x, values=spec.y, title=spec.title)
    elif spec.chart_type == "heatmap":
        numeric = df.select_dtypes(include="number")
        if numeric.empty:
            fig = go.Figure()
        else:
            fig = px.imshow(numeric.corr(numeric_only=True), title=spec.title, aspect="auto", color_continuous_scale="Viridis")
    else:
        fig = go.Figure()

    fig.update_layout(
        template="plotly_white",
        title=spec.title,
        xaxis_title=spec.x,
        yaxis_title=spec.y,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def figure_to_html(fig: go.Figure, *, full_html: bool = False) -> str:
    return fig.to_html(full_html=full_html, include_plotlyjs="cdn")


def figure_to_json(fig: go.Figure) -> dict[str, Any]:
    return json.loads(json.dumps(fig, cls=PlotlyJSONEncoder))


def render_bundle(df: pd.DataFrame, spec: ChartSpec) -> dict[str, Any]:
    fig = render_figure(df, spec)
    preview = df.head(50)
    return {
        "spec": spec.model_dump(),
        "figure": figure_to_json(fig),
        "html": figure_to_html(fig, full_html=False),
        "preview": preview.where(pd.notnull(preview), None).to_dict(orient="records"),
        "row_count": len(df),
        "columns": list(df.columns),
    }
