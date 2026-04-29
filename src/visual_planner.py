"""Plan an answer to a natural-language question against the semantic model.

This is the *first* LLM call in the pipeline. It returns a structured
plan that says either:

  - mode = 'existing':   we can answer with existing measures + columns.
                         Specifies the visualType, axis, value, optional
                         filter, slicer hints.
  - mode = 'custom_sql': we need a new SQL-backed table. Specifies the
                         columns we want and what the visual should show
                         on top of that new table.

The hybrid policy lives entirely in the prompt: the LLM is told to
prefer 'existing' and only fall back to 'custom_sql' when measures
genuinely don't cover the question.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from langchain_groq import ChatGroq

from .config import SETTINGS
from .plan_types import CustomSqlPlan, ExistingPlan, FieldRef, Plan, VisualType  # noqa: F401
from .schema_loader import SemanticModel, schema_summary_for_llm


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------

PLANNER_SYSTEM = """You are a Power BI report planner. You convert a \
natural-language business question into a JSON plan that a downstream code \
generator will use to build a new Power BI report (PBIP format).

You work against a fixed semantic model whose schema is provided below.

Strict rules:
1. PREFER mode='existing' whenever the existing measures + columns can \
answer the question. Only fall back to mode='custom_sql' when:
   - the question requires a row-level detail not exposed by any measure, OR
   - the question asks for a non-trivial server-side filter or aggregation \
not expressible as a simple measure-by-column slice (e.g. "top N", \
"customers who bought X but not Y", a custom date window past what \
existing measures provide).
2. Pick the smallest sensible visual_type:
   - cardVisual   : single number / KPI
   - lineChart    : trend over time (date or month axis)
   - barChart     : few categorical values on Y, measure on X
   - columnChart  : few categorical values on X, measure on Y
   - donutChart   : share-of-total across <=6 categories
   - tableEx      : rows of details, top N lists, multi-column comparisons
3. For mode='existing', every FieldRef.table + FieldRef.name MUST exist \
verbatim in the schema below. Do not invent fields.
4. For mode='custom_sql', expected_columns are the names the SQL will \
return; a separate step will write the actual SQL. Do not write SQL here.
5. Output ONLY valid JSON matching the schema. No prose, no markdown \
fences, no comments.

Plan JSON shape:
{
  "mode": "existing" | "custom_sql",
  "visual_type": "<one of the visual_type values>",
  "page_title": "<short title for the report page>",
  "visual_title": "<short title shown above the visual>",
  // when mode == "existing":
  "category": [{"table": "...", "name": "...", "kind": "column"|"measure"}],
  "values":   [{"table": "...", "name": "...", "kind": "column"|"measure"}],
  // when mode == "custom_sql":
  "new_table_name": "snake_case_no_spaces",
  "expected_columns": ["col1", "col2", ...],
  "category_columns": ["col1"],
  "value_columns":    ["col2"],
  "rationale": "one sentence"
}
"""


PLANNER_USER_TMPL = """{schema}

---

Business question: {question}

Return the JSON plan now."""


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(s: str) -> str:
    return _JSON_FENCE_RE.sub("", s).strip()


def plan_for_question(question: str, sm: SemanticModel) -> Plan:
    llm = ChatGroq(
        model=SETTINGS.groq_model,
        temperature=0,
        api_key=SETTINGS.groq_api_key,
    )
    schema = schema_summary_for_llm(sm)
    user = PLANNER_USER_TMPL.format(schema=schema, question=question)

    resp = llm.invoke(
        [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": user},
        ]
    )
    raw = _strip_fences(str(resp.content))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned non-JSON: {e}\n---\n{raw}") from e

    mode = data.get("mode")
    if mode == "existing":
        return ExistingPlan(**data)
    if mode == "custom_sql":
        return CustomSqlPlan(**data)
    raise ValueError(f"Planner returned unknown mode {mode!r}: {raw}")
