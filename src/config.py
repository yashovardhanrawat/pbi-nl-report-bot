"""Central configuration. All env vars and shared paths funnel through here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # LLM
    groq_api_key: str
    groq_model: str

    # Power BI / Fabric workspace
    pbi_workspace_id: str
    pbi_dataset_id: str

    # Fabric SQL endpoint
    fabric_sql_server: str
    fabric_sql_database: str

    # Paths
    reference_model_path: Path
    reports_out_dir: Path
    templates_dir: Path


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required env var {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


def load_settings() -> Settings:
    return Settings(
        groq_api_key=_required("GROQ_API_KEY"),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        pbi_workspace_id=_required("PBI_WORKSPACE_ID"),
        pbi_dataset_id=_required("PBI_DATASET_ID"),
        fabric_sql_server=_required("FABRIC_SQL_SERVER"),
        fabric_sql_database=_required("FABRIC_SQL_DATABASE"),
        reference_model_path=(
            PROJECT_ROOT
            / os.getenv(
                "REFERENCE_MODEL_PATH",
                "reference_model/EcommerceAnalytics.SemanticModel/model.bim",
            )
        ),
        reports_out_dir=PROJECT_ROOT / os.getenv("REPORTS_OUT_DIR", "reports_out"),
        templates_dir=PROJECT_ROOT / "templates",
    )


# Singleton — import this elsewhere
SETTINGS = load_settings()
