"""Generate a self-contained Power BI Project (PBIP) folder from a Plan.

A PBIP folder looks like:

    <ReportName>.pbip                                  (top-level wrapper)
    <ReportName>.Report/
        .platform
        definition.pbir
        definition/
            report.json
            version.json
            pages/
                pages.json
                <page-id>/
                    page.json
                    visuals/
                        <visual-id>/
                            visual.json
    <ReportName>.SemanticModel/
        .platform
        definition.pbism
        model.bim

For mode='existing' we COPY the reference model.bim verbatim — the new
report just references its existing tables and measures.

For mode='custom_sql' we LOAD the reference model.bim, ADD a new
partition (the LLM's SQL wrapped in a Value.NativeQuery M expression),
and write that out as the report's semantic model. The new visual then
references the new table directly.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import uuid
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .plan_types import CustomSqlPlan, ExistingPlan, FieldRef, Plan
from .schema_loader import SemanticModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s[:max_len] or "report"


def _hex_id(seed: str) -> str:
    """20-hex-char id, matching the visual.json `name` style in PBIP."""
    return hashlib.sha1(seed.encode() + secrets.token_bytes(8)).hexdigest()[:20]


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Static skeletons
# ---------------------------------------------------------------------------

def _platform_payload(item_type: str, display_name: str) -> dict:
    """The .platform file PBIP needs for each item folder."""
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": item_type,  # "Report" or "SemanticModel"
            "displayName": display_name,
        },
        "config": {
            "version": "2.0",
            "logicalId": str(uuid.uuid4()),
        },
    }


def _pbip_root(report_dir_name: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": report_dir_name}}],
        "settings": {"enableAutoRecovery": True},
    }


def _pbir(model_dir_name: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{model_dir_name}"}},
    }


def _pbism() -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2",
        "settings": {},
    }

def _report_root() -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.1.0/schema.json",
        "themeCollection": {
            "baseTheme": {
                "name": "CY24SU06",
                "reportVersionAtImport": "5.58",
                "type": "SharedResources",
            }
        },
        "objects": {
            "section": [
                {
                    "properties": {
                        "verticalAlignment": {"expr": {"Literal": {"Value": "'Top'"}}}
                    }
                }
            ]
        },
        "settings": {
            "useStylableVisualContainerHeader": True,
            "exportDataMode": "AllowSummarized",
            "defaultDrillFilterOtherVisuals": True,
            "allowChangeFilterTypes": True,
            "useEnhancedTooltips": True,
            "useDefaultAggregateDisplayName": True,
        },
    }


def _version_meta() -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    }


# ---------------------------------------------------------------------------
# Visual builders — one per visual_type
# ---------------------------------------------------------------------------

def _field_block(table: str, name: str, kind: str) -> dict:
    """Build the {field: {Column|Measure: {...}}} block used in queryState."""
    src_ref = {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}
    if kind == "measure":
        return {
            "field": {"Measure": src_ref},
            "queryRef": f"{table}.{name}",
            "nativeQueryRef": name,
        }
    return {
        "field": {"Column": src_ref},
        "queryRef": f"{table}.{name}",
        "nativeQueryRef": name,
        "active": True,
    }


def _build_visual_json(
    name: str,
    visual_type: str,
    title: str,
    position: dict,
    category_fields: list[FieldRef],
    value_fields: list[FieldRef],
) -> dict:
    """Build a visual.json dict for any of the supported visual_types.

    The PBIP visual schema uses different role names per visual; we use
    'Category' / 'Y' / 'Data' / 'Values' to match what Power BI Desktop
    emits when you build the same chart manually.
    """
    role_for_category, role_for_value = _roles_for(visual_type)

    query_state: dict[str, Any] = {}

    # Special case: tableEx wants every projection in a single 'Values' role.
    if visual_type == "tableEx":
        merged = [
            _field_block(f.table, f.name, f.kind)
            for f in (*category_fields, *value_fields)
        ]
        if merged:
            query_state["Values"] = {"projections": merged}
    else:
        if category_fields and role_for_category:
            query_state[role_for_category] = {
                "projections": [
                    _field_block(f.table, f.name, f.kind) for f in category_fields
                ]
            }
        if value_fields and role_for_value:
            query_state[role_for_value] = {
                "projections": [
                    _field_block(f.table, f.name, f.kind) for f in value_fields
                ]
            }

    visual: dict[str, Any] = {
        "visualType": visual_type,
        "query": {"queryState": query_state},
        "drillFilterOtherVisuals": True,
    }

    # Sensible default: sort by the first measure descending where applicable
    first_measure = next(
        (f for f in value_fields if f.kind == "measure"),
        next(iter(value_fields), None),
    )
    if first_measure and visual_type != "cardVisual":
        visual["query"]["sortDefinition"] = {
            "sort": [
                {
                    "field": {
                        ("Measure" if first_measure.kind == "measure" else "Column"): {
                            "Expression": {"SourceRef": {"Entity": first_measure.table}},
                            "Property": first_measure.name,
                        }
                    },
                    "direction": "Descending",
                }
            ],
            "isDefaultSort": True,
        }

    if visual_type == "donutChart":
        visual["objects"] = {
            "labels": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "labelStyle": {
                            "expr": {"Literal": {"Value": "'Category, percent of total'"}}
                        },
                    }
                }
            ]
        }

    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json",
        "name": name,
        "position": position,
        "visual": visual,
    }


def _roles_for(visual_type: str) -> tuple[str | None, str | None]:
    """Return (category_role, value_role) for a given Power BI visual_type.

    Note: tableEx is special — it merges category + value into a single
    'Values' role and is handled in _build_visual_json directly.
    """
    return {
        "cardVisual": (None, "Data"),
        "lineChart": ("Category", "Y"),
        "barChart": ("Category", "X"),
        "columnChart": ("Category", "Y"),
        "donutChart": ("Category", "Y"),
        "tableEx": ("Values", "Values"),  # not actually used; see special case
    }.get(visual_type, ("Category", "Y"))


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------

def _semantic_model_for_existing(
    sm: SemanticModel, target_dir: Path
) -> None:
    """Copy reference model verbatim — no new tables needed."""
    src_root = SETTINGS.reference_model_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    # copy model.bim
    shutil.copyfile(SETTINGS.reference_model_path, target_dir / "model.bim")
    # copy definition.pbism if present, otherwise emit a fresh one
    pbism_src = src_root / "definition.pbism"
    if pbism_src.exists():
        shutil.copyfile(pbism_src, target_dir / "definition.pbism")
    else:
        _write_json(target_dir / "definition.pbism", _pbism())


def _semantic_model_for_custom_sql(
    sm: SemanticModel,
    plan: CustomSqlPlan,
    sql: str,
    target_dir: Path,
) -> None:
    """Load the reference model, add a new SQL-backed table, write it out."""
    bim = copy.deepcopy(sm.raw)

    # Find any existing table with a Fabric Sql.Database source so we
    # can copy its server/db. Default to env config if none found.
    server = SETTINGS.fabric_sql_server
    database = SETTINGS.fabric_sql_database
    for t in sm.tables:
        if t.fabric_server and t.fabric_database:
            server, database = t.fabric_server, t.fabric_database
            break

    # Escape any double quotes in the SQL for the M expression
    sql_for_m = sql.replace("\\", "\\\\").replace('"', '""')

    new_table = {
        "name": plan.new_table_name,
        "lineageTag": _hex_id(plan.new_table_name),
        "columns": [
            {
                "name": col,
                "dataType": "string",  # safe default; PBI infers on refresh
                "sourceColumn": col,
                "summarizeBy": "none",
                "lineageTag": _hex_id(plan.new_table_name + col),
            }
            for col in plan.expected_columns
        ],
        "partitions": [
            {
                "name": plan.new_table_name,
                "mode": "import",
                "source": {
                    "type": "m",
                    "expression": [
                        "let",
                        f'    Source    = Sql.Database("{server}", "{database}", '
                        "[CreateNavigationProperties=false]),",
                        f'    SqlResult = Value.NativeQuery(Source, "{sql_for_m}", '
                        "null, [EnableFolding=false])",
                        "in  SqlResult",
                    ],
                },
            }
        ],
    }
    bim["model"]["tables"].append(new_table)

    target_dir.mkdir(parents=True, exist_ok=True)
    _write_json(target_dir / "model.bim", bim)
    _write_json(target_dir / "definition.pbism", _pbism())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate_pbip(
    *,
    question: str,
    plan: Plan,
    sm: SemanticModel,
    sql: str | None = None,
) -> Path:
    """Generate a fresh PBIP folder for this question. Returns its root path."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = _slug(plan.page_title or question)
    report_root = SETTINGS.reports_out_dir / f"{slug}_{timestamp}"
    report_root.mkdir(parents=True, exist_ok=True)

    report_name = "GeneratedReport"
    report_dir_name = f"{report_name}.Report"
    model_dir_name = f"{report_name}.SemanticModel"

    report_dir = report_root / report_dir_name
    model_dir = report_root / model_dir_name

    # ---------------- Semantic model ----------------
    if isinstance(plan, CustomSqlPlan):
        if not sql:
            raise ValueError("custom_sql plan requires a non-empty sql string")
        _semantic_model_for_custom_sql(sm, plan, sql, model_dir)
    else:
        _semantic_model_for_existing(sm, model_dir)

    _write_json(model_dir / ".platform", _platform_payload("SemanticModel", report_name))

    # ---------------- Report shell ----------------
    _write_json(report_dir / ".platform", _platform_payload("Report", report_name))
    _write_json(report_dir / "definition.pbir", _pbir(model_dir_name))
    defn = report_dir / "definition"
    _write_json(defn / "report.json", _report_root())
    _write_json(defn / "version.json", _version_meta())

    # ---------------- Page + visual ----------------
    page_id = _hex_id(plan.page_title or question)
    page_dir = defn / "pages" / page_id
    _write_json(
        defn / "pages" / "pages.json",
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": [page_id],
            "activePageName": page_id,
        },
    )
    _write_json(
        page_dir / "page.json",
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
            "name": page_id,
            "displayName": plan.page_title,
            "displayOption": "FitToPage",
            "height": 720,
            "width": 1280,
        },
    )

    if isinstance(plan, ExistingPlan):
        cat = plan.category
        val = plan.values
    else:  # CustomSqlPlan
        cat = [
            FieldRef(table=plan.new_table_name, name=c, kind="column")
            for c in plan.category_columns
        ]
        val = [
            FieldRef(table=plan.new_table_name, name=c, kind="column")
            for c in plan.value_columns
        ]

    visual_id = _hex_id(plan.visual_title)
    visual_json = _build_visual_json(
        name=visual_id,
        visual_type=plan.visual_type,
        title=plan.visual_title,
        position={
            "x": 40,
            "y": 60,
            "z": 1000,
            "height": 580,
            "width": 1200,
            "tabOrder": 1000,
        },
        category_fields=cat,
        value_fields=val,
    )
    _write_json(page_dir / "visuals" / visual_id / "visual.json", visual_json)

    # ---------------- PBIP wrapper ----------------
    _write_json(report_root / f"{report_name}.pbip", _pbip_root(report_dir_name))

    # Convenience: a sidecar manifest that records what we generated
    sidecar = {
        "question": question,
        "plan": plan.model_dump(),
        "sql": sql,
        "generated_at_utc": timestamp,
    }
    _write_json(report_root / "_manifest.json", sidecar)

    return report_root
