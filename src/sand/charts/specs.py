"""Chart specification models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChartType = Literal["bar", "line", "scatter", "pie", "heatmap", "table"]


class ChartSpec(BaseModel):
    chart_type: ChartType = "bar"
    title: str = "Chart"
    x: str | None = None
    y: str | None = None
    color: str | None = None
    orientation: Literal["v", "h"] = "v"
    reason: str = Field(default="", description="Why this chart type was chosen")
