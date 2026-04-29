"""Generate Fabric T-SQL for a question that the planner flagged as
needing custom SQL.

Only the custom_sql branch hits this module. The output SQL must:
  - target the Fabric Warehouse (T-SQL dialect, dbo schema)
  - return columns whose names exactly match plan.expected_columns
  - be a SELECT statement (no DDL, no DML, no semicolons-in-the-middle
    that hint at multiple statements).
"""

from __future__ import annotations

import re

from langchain_groq import ChatGroq

from .config import SETTINGS
from .plan_types import CustomSqlPlan
from .schema_loader import SemanticModel, schema_summary_for_llm


SQLGEN_SYSTEM = """You are a T-SQL expert who writes queries for \
Microsoft Fabric Warehouse.

Rules:
1. The query MUST be a single SELECT statement targeting the dbo schema \
of the Fabric Warehouse described in the schema below. No CTE-after-DDL, \
no INSERT/UPDATE/DELETE, no stored procedures.
2. Output column names MUST exactly match the expected_columns list \
provided (use AS to alias if needed). The order should match too.
3. Use ANSI / T-SQL syntax. For "last N months", use \
DATEADD(month, -N, CAST(GETDATE() AS DATE)) on the fact_sales.date column.
4. Output ONLY the SQL. No markdown fences, no comments, no trailing \
semicolons, no prose.
"""


SQLGEN_USER_TMPL = """{schema}

---

Business question: {question}

Required output columns (in this exact order, with these exact names):
{expected_columns}

Suggested table name (for documentation only): {new_table_name}

Write the SELECT statement now."""


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|exec|merge)\b",
    re.IGNORECASE,
)
_FENCES = re.compile(r"^\s*```(?:sql|tsql)?\s*|\s*```\s*$", re.MULTILINE)


def _sanitize(sql: str) -> str:
    sql = _FENCES.sub("", sql).strip()
    # collapse trailing semicolons
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return sql


def generate_sql(question: str, plan: CustomSqlPlan, sm: SemanticModel) -> str:
    llm = ChatGroq(
        model=SETTINGS.groq_model,
        temperature=0,
        api_key=SETTINGS.groq_api_key,
    )
    schema = schema_summary_for_llm(sm)
    user = SQLGEN_USER_TMPL.format(
        schema=schema,
        question=question,
        expected_columns=", ".join(plan.expected_columns),
        new_table_name=plan.new_table_name,
    )

    resp = llm.invoke(
        [
            {"role": "system", "content": SQLGEN_SYSTEM},
            {"role": "user", "content": user},
        ]
    )
    sql = _sanitize(str(resp.content))

    if not sql.lower().lstrip().startswith(("select", "with")):
        raise ValueError(f"Generated SQL doesn't start with SELECT/WITH:\n{sql}")
    if _FORBIDDEN.search(sql):
        raise ValueError(f"Generated SQL contains forbidden keywords:\n{sql}")
    return sql
