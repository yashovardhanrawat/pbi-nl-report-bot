"""Dry-run the generator without the LLM. Useful for testing the PBIP
folder layout against Power BI Desktop without burning Groq tokens.

Usage:
    python -m scripts.dry_run existing
    python -m scripts.dry_run custom
"""

from __future__ import annotations

import sys

from src.config import SETTINGS
from src.pbip_generator import generate_pbip
from src.plan_types import CustomSqlPlan, ExistingPlan, FieldRef
from src.schema_loader import load_semantic_model


def _existing_demo() -> None:
    sm = load_semantic_model(SETTINGS.reference_model_path)
    plan = ExistingPlan(
        visual_type="columnChart",
        page_title="Revenue by Region",
        visual_title="Total Revenue by Region",
        category=[FieldRef(table="fact_sales", name="region", kind="column")],
        values=[FieldRef(table="fact_sales", name="Total Revenue", kind="measure")],
        rationale="The Total Revenue measure exists; region is a column.",
    )
    out = generate_pbip(
        question="What is total revenue by region?",
        plan=plan,
        sm=sm,
    )
    print("Existing-mode PBIP at:", out)


def _custom_demo() -> None:
    sm = load_semantic_model(SETTINGS.reference_model_path)
    plan = CustomSqlPlan(
        visual_type="tableEx",
        page_title="Top 5 products last 90 days",
        visual_title="Top 5 products by net revenue (last 90 days)",
        new_table_name="q_top5_products_last_90d",
        expected_columns=["product_name", "net_revenue"],
        category_columns=["product_name"],
        value_columns=["net_revenue"],
        rationale="Top-N is not exposed by any existing measure; needs a SELECT TOP 5.",
    )
    sql = (
        "SELECT TOP 5 dp.product_name, SUM(fs.net_revenue) AS net_revenue "
        "FROM dbo.fact_sales fs "
        "JOIN dbo.dim_product dp ON fs.product_id = dp.product_id "
        "WHERE fs.date >= DATEADD(day, -90, CAST(GETDATE() AS DATE)) "
        "GROUP BY dp.product_name "
        "ORDER BY net_revenue DESC"
    )
    out = generate_pbip(
        question="Top 5 products by net revenue in the last 90 days",
        plan=plan,
        sm=sm,
        sql=sql,
    )
    print("Custom-SQL PBIP at:", out)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "existing"
    if mode == "existing":
        _existing_demo()
    elif mode == "custom":
        _custom_demo()
    else:
        print("usage: python -m scripts.dry_run [existing|custom]", file=sys.stderr)
        sys.exit(1)
