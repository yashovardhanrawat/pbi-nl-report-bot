"""LangGraph wiring: NL question -> plan -> (SQL -> validate)? -> PBIP.

Graph:

    [load_schema]
          v
        [plan]
          v
       <branch>
        |       \
   existing    custom_sql
        |          v
        |     [generate_sql]
        |          v
        |     [validate_sql]    -- on failure, retry up to MAX_SQL_RETRIES
        v          v
    [generate_pbip]
          v
        END
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

from .config import SETTINGS
from .nl_to_sql import generate_sql
from .pbip_generator import generate_pbip
from .plan_types import CustomSqlPlan, ExistingPlan, Plan
from .schema_loader import SemanticModel, load_semantic_model
from .sql_validator import validate_sql
from .visual_planner import plan_for_question


MAX_SQL_RETRIES = 2


class GraphState(TypedDict, total=False):
    # input
    question: str

    # populated as the graph progresses
    schema: SemanticModel
    plan: Plan
    sql: str
    sql_validation_error: str | None
    sql_attempts: int
    output_path: Path
    error: str


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _load_schema(state: GraphState) -> GraphState:
    sm = load_semantic_model(SETTINGS.reference_model_path)
    return {"schema": sm}


def _plan(state: GraphState) -> GraphState:
    sm = state["schema"]
    plan = plan_for_question(state["question"], sm)
    return {"plan": plan, "sql_attempts": 0}


def _generate_sql(state: GraphState) -> GraphState:
    plan = state["plan"]
    assert isinstance(plan, CustomSqlPlan)
    sql = generate_sql(state["question"], plan, state["schema"])
    return {"sql": sql, "sql_attempts": state.get("sql_attempts", 0) + 1}


def _validate_sql(state: GraphState) -> GraphState:
    result = validate_sql(state["sql"])
    return {"sql_validation_error": None if result.ok else result.error}


def _generate_pbip(state: GraphState) -> GraphState:
    out = generate_pbip(
        question=state["question"],
        plan=state["plan"],
        sm=state["schema"],
        sql=state.get("sql"),
    )
    return {"output_path": out}


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------

def _route_after_plan(state: GraphState) -> Literal["custom_sql", "skip_sql"]:
    return "custom_sql" if isinstance(state["plan"], CustomSqlPlan) else "skip_sql"


def _route_after_validation(state: GraphState) -> Literal["retry", "ok"]:
    if not state.get("sql_validation_error"):
        return "ok"
    if state.get("sql_attempts", 0) >= MAX_SQL_RETRIES:
        return "ok"  # give up retrying — write the report anyway, error is in manifest
    return "retry"


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def build_graph():
    g = StateGraph(GraphState)
    g.add_node("load_schema", _load_schema)
    g.add_node("plan", _plan)
    g.add_node("generate_sql", _generate_sql)
    g.add_node("validate_sql", _validate_sql)
    g.add_node("generate_pbip", _generate_pbip)

    g.set_entry_point("load_schema")
    g.add_edge("load_schema", "plan")
    g.add_conditional_edges(
        "plan",
        _route_after_plan,
        {"custom_sql": "generate_sql", "skip_sql": "generate_pbip"},
    )
    g.add_edge("generate_sql", "validate_sql")
    g.add_conditional_edges(
        "validate_sql",
        _route_after_validation,
        {"retry": "generate_sql", "ok": "generate_pbip"},
    )
    g.add_edge("generate_pbip", END)
    return g.compile()


def run(question: str) -> GraphState:
    graph = build_graph()
    final = graph.invoke({"question": question})
    return final  # type: ignore[return-value]
