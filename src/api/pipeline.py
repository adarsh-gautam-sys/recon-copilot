"""
Pipeline orchestration — wires together the four stages of Reconciliation Copilot.

Used by both the FastAPI layer (src/api/main.py) and the standalone demo script
(run_demo.py) so the logic lives in exactly one place.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.config import EscalatorConfig
from src.data_gen.generate_synthetic_data import generate_dataset
from src.matching.deterministic_matcher import (
    DeterministicMatcher,
    load_bank,
    load_gateway,
    load_ledger,
)
from src.matching.llm_escalator import LLMEscalator
from src.reporting.audit_report import AuditReport, ReportGenerator


# ---------------------------------------------------------------------------
# Request parameters
# ---------------------------------------------------------------------------

@dataclass
class PipelineRequest:
    """All tuneable knobs for a single pipeline run."""
    n_rows: int = 300
    seed: int = 42
    use_existing_data: bool = True          # skip generation when CSVs already exist
    llm_mode: str = "offline"               # "offline" | "openrouter"
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "z-ai/glm-5.2:free"
    llm_delay_ms: int = 2000               # inter-call delay (ms) for free-tier APIs


# ---------------------------------------------------------------------------
# Offline mock (no API key required)
# ---------------------------------------------------------------------------

class _OfflineMockClient:
    """
    Returns 'no match / offline' for every row.
    Used when llm_mode='offline' or no API key is available.
    All LLM rows become unresolved with exception_category='no_llm_match'.
    """
    def complete(self, system: str, user: str) -> str:
        return json.dumps({
            "proposed_match": None,
            "reasoning": "Offline mode — no LLM API key configured.",
            "confidence": 0.0,
        })


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_pipeline(
    req: PipelineRequest,
    data_dir: Path,
    outputs_dir: Path,
) -> AuditReport:
    """
    Run the full reconciliation pipeline and return an AuditReport.

    Stages
    ------
    1. Data generation (skipped when use_existing_data=True and CSVs exist).
    2. Deterministic matcher (rule-based, confidence 1.0).
    3. LLM escalator (offline mock or live OpenRouter call).
    4. Audit report generator (writes JSON to outputs_dir, prints summary).
    """
    # Stage 1 — generate or load data
    ledger_path = data_dir / "internal_ledger.csv"
    if not req.use_existing_data or not ledger_path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        generate_dataset(n_rows=req.n_rows, seed=req.seed, output_dir=data_dir)

    ledger  = load_ledger(data_dir / "internal_ledger.csv")
    bank    = load_bank(data_dir / "bank_settlement.csv")
    gateway = load_gateway(data_dir / "gateway_settlement.csv")

    # Stage 2 — deterministic match
    matcher = DeterministicMatcher()
    rule_matches, unmatched = matcher.match(ledger, bank, gateway)

    # Stage 3 — LLM escalate
    llm_client = _build_llm_client(req)
    cfg = EscalatorConfig(
        confidence_threshold=0.70,
        call_delay_seconds=req.llm_delay_ms / 1000.0,
    )
    escalator = LLMEscalator(client=llm_client, config=cfg)
    llm_results = escalator.escalate(unmatched, bank, gateway)

    # Stage 4 — audit report
    gen = ReportGenerator(outputs_dir=outputs_dir)
    return gen.generate(
        rule_matches + llm_results,
        data_dir / "answer_key.csv",
    )


def _build_llm_client(req: PipelineRequest):
    """Return the appropriate LLM client for the given mode."""
    if req.llm_mode == "openrouter":
        key = req.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")
        if key:
            from src.matching.llm_escalator import OpenRouterLLMClient
            return OpenRouterLLMClient(model=req.openrouter_model, api_key=key)
    # Fallback: offline mock
    return _OfflineMockClient()
