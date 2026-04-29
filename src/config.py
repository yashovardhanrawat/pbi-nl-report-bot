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

    # LangSmith
    langsmith_tracing: bool
    langsmith_project: str


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required env var {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


def _setup_langsmith() -> None:
    """Configure LangSmith tracing if env vars are present."""
    if os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true":
        api_key = os.getenv("LANGCHAIN_API_KEY", "")
        if not api_key:
            import warnings
            warnings.warn(
                "LANGCHAIN_TRACING_V2=true but LANGCHAIN_API_KEY is not set. "
                "Tracing will be disabled.",
                stacklevel=2,
            )
            os.environ["LANGCHAIN_TRACING_V2"] = "false"
        else:
            # Ensure the endpoint is set
            if not os.getenv("LANGCHAIN_ENDPOINT"):
                os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
            print(
                f"[LangSmith] Tracing enabled → project: "
                f"{os.getenv('LANGCHAIN_PROJECT', 'default')}"
            )


def load_settings() -> Settings:
    _setup_langsmith()
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
        langsmith_tracing=os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true",
        langsmith_project=os.getenv("LANGCHAIN_PROJECT", "pbi-langchain-poc"),
    )


# Singleton — import this elsewhere
SETTINGS = load_settings()