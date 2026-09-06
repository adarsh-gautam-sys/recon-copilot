"""
Reconciliation Copilot — FastAPI entry point (PRD §7.5)

Endpoints
---------
GET  /health          — liveness probe
POST /reconcile       — run the full pipeline, return the audit report as JSON
GET  /report/{run_id} — fetch a previously generated report from outputs/

Usage (development server):
    uvicorn src.api.main:app --reload --port 8000

Environment variables:
    DATA_DIR     — path to CSV data directory (default: data/)
    OUTPUTS_DIR  — path to report output directory (default: outputs/)
    OPENROUTER_API_KEY — optional; enables live LLM calls when llm_mode="openrouter"
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.pipeline import PipelineRequest, run_pipeline

# ---------------------------------------------------------------------------
# Paths (overridable via env vars for testing)
# ---------------------------------------------------------------------------

DATA_DIR    = Path(os.environ.get("DATA_DIR",    "data"))
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", "outputs"))

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Reconciliation Copilot",
    description=(
        "A deterministic-first reconciliation agent that matches transactions "
        "across bank settlement, payment-gateway settlement, and internal ledger "
        "sources — escalating only genuinely ambiguous rows to an LLM."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str


class ReconcileRequest(BaseModel):
    """Request body for POST /reconcile."""
    n_rows: int = Field(300, ge=1, le=10_000, description="Number of ledger rows to generate")
    seed: int = Field(42, description="Random seed for reproducibility")
    use_existing_data: bool = Field(
        True,
        description="If True and data/ CSVs already exist, skip generation and load them directly."
    )
    llm_mode: str = Field(
        "offline",
        description="'offline' (no API calls) or 'openrouter' (live LLM via OpenRouter)."
    )
    openrouter_api_key: Optional[str] = Field(
        None,
        description="OpenRouter API key. Falls back to OPENROUTER_API_KEY env var."
    )
    openrouter_model: str = Field(
        "z-ai/glm-5.2:free",
        description="OpenRouter model slug."
    )
    llm_delay_ms: int = Field(
        2000,
        ge=0,
        description="Milliseconds to wait between LLM API calls (helps with free-tier rate limits)."
    )


class NoiseSummaryEntry(BaseModel):
    total: int
    matched: int
    unresolved: int
    correctly_matched: int


class ReconcileResponse(BaseModel):
    """Top-level audit report returned by POST /reconcile."""
    run_id: str
    generated_at: str
    total_rows: int
    rule_matched: int
    llm_matched: int
    unresolved: int
    rule_match_rate: float
    llm_match_rate: float
    total_match_rate: float
    unresolved_rate: float
    verified_match_rate: float
    llm_precision: Optional[float]
    red_flag: bool
    exception_breakdown: Dict[str, int]
    noise_type_summary: Dict[str, NoiseSummaryEntry]
    per_row_audit: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Simple liveness probe — returns 200 if the service is up."""
    return HealthResponse(status="ok", version="1.0.0")


@app.post("/reconcile", response_model=ReconcileResponse, tags=["pipeline"])
def reconcile(body: ReconcileRequest) -> ReconcileResponse:
    """
    Run the full reconciliation pipeline.

    1. Generate synthetic CSV data (or load existing ones).
    2. Apply the deterministic rule-based matcher.
    3. Escalate unmatched rows to the LLM (or offline mock).
    4. Generate audit report, save to outputs/, and return as JSON.
    """
    req = PipelineRequest(
        n_rows=body.n_rows,
        seed=body.seed,
        use_existing_data=body.use_existing_data,
        llm_mode=body.llm_mode,
        openrouter_api_key=body.openrouter_api_key,
        openrouter_model=body.openrouter_model,
        llm_delay_ms=body.llm_delay_ms,
    )
    try:
        report = run_pipeline(req, DATA_DIR, OUTPUTS_DIR)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ReconcileResponse(**asdict(report))


@app.get("/report/{run_id}", tags=["pipeline"])
def get_report(run_id: str) -> JSONResponse:
    """
    Retrieve a previously generated report by its run_id (timestamp string).

    The run_id is the value returned in the `run_id` field of a prior /reconcile response.
    Reports are stored in outputs/report_{run_id}.json.
    """
    report_path = OUTPUTS_DIR / f"report_{run_id}.json"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report '{run_id}' not found. "
                   f"Available reports: {_list_run_ids()}",
        )
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _list_run_ids() -> list[str]:
    """Return all known run_ids from the outputs directory."""
    if not OUTPUTS_DIR.exists():
        return []
    return [
        p.stem.replace("report_", "")
        for p in sorted(OUTPUTS_DIR.glob("report_*.json"))
    ]
