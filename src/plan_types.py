"""Plan dataclasses used by both the planner (which calls the LLM) and
the PBIP generator (which doesn't). Splitting them out keeps
pbip_generator import-able without langchain installed — useful for
dry-run tests and CI.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VisualType = Literal[
    "cardVisual",
    "lineChart",
    "barChart",
    "columnChart",
    "donutChart",
    "tableEx",
]


class FieldRef(BaseModel):
    table: str
    name: str
    kind: Literal["column", "measure"]


class ExistingPlan(BaseModel):
    mode: Literal["existing"] = "existing"
    visual_type: VisualType
    page_title: str
    visual_title: str
    category: list[FieldRef] = Field(default_factory=list)
    values: list[FieldRef] = Field(default_factory=list)
    rationale: str = ""


class CustomSqlPlan(BaseModel):
    mode: Literal["custom_sql"] = "custom_sql"
    visual_type: VisualType
    page_title: str
    visual_title: str
    new_table_name: str
    expected_columns: list[str]
    category_columns: list[str] = Field(default_factory=list)
    value_columns: list[str] = Field(default_factory=list)
    rationale: str = ""


Plan = ExistingPlan | CustomSqlPlan
