"""Parse model.bim into a structured schema and a compact text summary
that the LLM can reason over.

The LLM never sees the full model.bim (too noisy). It sees a focused
schema-context block produced by `schema_summary_for_llm()`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Column:
    name: str
    data_type: str
    source_column: str | None = None
    is_calculated: bool = False


@dataclass
class Measure:
    name: str
    expression: str  # DAX


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    source_sql: str | None = None  # extracted from M-expression Value.NativeQuery
    fabric_server: str | None = None
    fabric_database: str | None = None


@dataclass
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    is_active: bool = True


@dataclass
class SemanticModel:
    tables: list[Table]
    relationships: list[Relationship]
    raw: dict[str, Any]  # the full parsed bim, kept around in case generators need it


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _join_expression(expr: Any) -> str:
    """Power BI stores M / DAX expressions as either a string or a list of lines."""
    if isinstance(expr, list):
        return "\n".join(expr)
    return str(expr or "")


def _extract_native_sql(m_expression: str) -> str | None:
    """Pull the SQL string out of a Power Query M expression that uses
    Value.NativeQuery(Source, "<SQL>", null, [...]). Returns None if it's
    not that pattern (e.g. plain `SELECT * FROM dbo.x` via Sql.Database
    navigation, calculated tables, etc.).
    """
    marker = "Value.NativeQuery"
    idx = m_expression.find(marker)
    if idx == -1:
        return None
    # Find the first quoted string after the marker.
    after = m_expression[idx:]
    first_quote = after.find('"')
    if first_quote == -1:
        return None
    # Walk forward to the matching closing quote, honoring "" escapes.
    body = after[first_quote + 1:]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            if i + 1 < len(body) and body[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            break
        out.append(ch)
        i += 1
    return "".join(out).strip() or None


def _extract_fabric_endpoint(m_expression: str) -> tuple[str | None, str | None]:
    """Pull (server, database) out of Sql.Database("server", "db", ...)."""
    marker = "Sql.Database"
    idx = m_expression.find(marker)
    if idx == -1:
        return (None, None)
    after = m_expression[idx:]
    # naive: grab the first two double-quoted strings
    parts: list[str] = []
    i = 0
    while i < len(after) and len(parts) < 2:
        if after[i] == '"':
            j = i + 1
            buf: list[str] = []
            while j < len(after):
                if after[j] == '"' and not (j + 1 < len(after) and after[j + 1] == '"'):
                    break
                buf.append(after[j])
                j += 1
            parts.append("".join(buf))
            i = j + 1
            continue
        i += 1
    if len(parts) == 2:
        return (parts[0], parts[1])
    return (None, None)


def load_semantic_model(bim_path: Path) -> SemanticModel:
    raw = json.loads(Path(bim_path).read_text(encoding="utf-8-sig"))
    model = raw["model"]

    tables: list[Table] = []
    for t in model.get("tables", []):
        cols = [
            Column(
                name=c["name"],
                data_type=c.get("dataType", "unknown"),
                source_column=c.get("sourceColumn"),
                is_calculated=c.get("type") == "calculated",
            )
            for c in t.get("columns", [])
        ]
        meas = [
            Measure(
                name=m["name"],
                expression=_join_expression(m.get("expression", "")),
            )
            for m in t.get("measures", [])
        ]
        tbl = Table(name=t["name"], columns=cols, measures=meas)

        # Attempt to extract SQL + endpoint from the first M partition
        for p in t.get("partitions", []):
            src = p.get("source", {})
            if src.get("type") == "m":
                m_expr = _join_expression(src.get("expression", ""))
                tbl.source_sql = _extract_native_sql(m_expr)
                tbl.fabric_server, tbl.fabric_database = _extract_fabric_endpoint(m_expr)
                break
        tables.append(tbl)

    rels = [
        Relationship(
            from_table=r["fromTable"],
            from_column=r["fromColumn"],
            to_table=r["toTable"],
            to_column=r["toColumn"],
            is_active=r.get("isActive", True),
        )
        for r in model.get("relationships", [])
    ]

    return SemanticModel(tables=tables, relationships=rels, raw=raw)


# ---------------------------------------------------------------------------
# LLM-facing summary
# ---------------------------------------------------------------------------

def schema_summary_for_llm(sm: SemanticModel) -> str:
    """Return a compact markdown-ish summary of the schema. This is what
    gets injected into the LLM prompt — small enough to fit, rich enough
    to drive correct visual + SQL generation.
    """
    lines: list[str] = []
    lines.append("# Semantic Model Schema")
    lines.append("")

    lines.append("## Tables")
    for t in sm.tables:
        cols_str = ", ".join(f"{c.name}:{c.data_type}" for c in t.columns)
        lines.append(f"- **{t.name}** ({cols_str})")
        if t.measures:
            lines.append(f"  - measures:")
            for m in t.measures:
                # truncate long DAX
                expr = m.expression.replace("\n", " ")
                if len(expr) > 100:
                    expr = expr[:97] + "..."
                lines.append(f"    - `{m.name}` = {expr}")

    lines.append("")
    lines.append("## Relationships (active, single-direction unless noted)")
    for r in sm.relationships:
        suffix = "" if r.is_active else " [inactive]"
        lines.append(
            f"- {r.from_table}.{r.from_column} → {r.to_table}.{r.to_column}{suffix}"
        )

    # Mention the underlying SQL source — important for the SQL-generation prompt.
    fact = next((t for t in sm.tables if t.fabric_server), None)
    if fact:
        lines.append("")
        lines.append("## Underlying SQL source (Microsoft Fabric Warehouse, T-SQL dialect)")
        lines.append(f"- server: `{fact.fabric_server}`")
        lines.append(f"- database: `{fact.fabric_database}`")
        lines.append("- All physical tables live under the `dbo` schema.")
        lines.append("- Physical table names match the model table names "
                     "(e.g. `dbo.fact_sales`, `dbo.dim_product`, etc.).")

    return "\n".join(lines)


def measure_and_column_index(sm: SemanticModel) -> dict[str, list[str]]:
    """Flat index of available {table: [measure_or_column_names]} for
    quick lookups by the visual planner."""
    out: dict[str, list[str]] = {}
    for t in sm.tables:
        names = [c.name for c in t.columns] + [m.name for m in t.measures]
        out[t.name] = names
    return out
