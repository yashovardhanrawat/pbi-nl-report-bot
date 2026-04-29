"""CLI entry point.

Usage:
    python -m src.main "What is the total revenue by region last year?"
    python -m src.main         # interactive prompt
"""

from __future__ import annotations

import json
import sys

from .graph import run
from .plan_types import CustomSqlPlan


def _ask() -> str:
    print("Enter your business question (single line):")
    return input("> ").strip()


def main() -> int:
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:]).strip()
    else:
        question = _ask()

    if not question:
        print("No question given. Aborting.", file=sys.stderr)
        return 1

    print(f"\n=== NL → PBIP ===\nQuestion: {question}\n")
    state = run(question)

    plan = state.get("plan")
    if plan is not None:
        print("Plan:")
        print(json.dumps(plan.model_dump(), indent=2))
        print()

    if isinstance(plan, CustomSqlPlan):
        print("Generated SQL:")
        print(state.get("sql", "<missing>"))
        err = state.get("sql_validation_error")
        if err:
            print(f"\nSQL validation note: {err}")
        print()

    out = state.get("output_path")
    if out is None:
        print("ERROR: no output produced.", file=sys.stderr)
        return 2
    print(f"PBIP written to: {out}")
    print("Open the .pbip file inside that folder with Power BI Desktop "
          "(File → Open → choose the .pbip).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
