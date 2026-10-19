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
    llm_mode: str = "offline"               # "offline" | "heuristic" | "ollama" | "openrouter"
    confidence_threshold: float = 0.70      # code-enforced confidence gate
    ollama_model: str = "qwen3.1"           # "qwen3.1" | "gemma4"
    ollama_host: str = "https://ollama.com/v1"
    ollama_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "z-ai/glm-5.2:free"
    llm_delay_ms: int = 500                 # inter-call delay (ms) for free-tier APIs


# ---------------------------------------------------------------------------
# Offline mock & local heuristic agent simulator (no API key required)
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


class _HeuristicAgentClient:
    """
    Bounded local agent simulator for academic evaluation without external API keys.
    Inspects candidate bank narrations for order_id fragments and evaluates amount drift,
    emitting realistic confidence scores that exercise the code-level confidence gate.
    """
    def complete(self, system: str, user: str) -> str:
        import re
        ord_m = re.search(r"order_id\s*:\s*(\S+)", user)
        amt_m = re.search(r"amount\s*:\s*INR\s*([\d.]+)", user)
        order_id = ord_m.group(1) if ord_m else ""
        ledger_amt = float(amt_m.group(1)) if amt_m else 0.0
        ord_Fragment = order_id.split("_")[-1] if "_" in order_id else order_id

        cand_pattern = re.compile(
            r'Bank UTR=(\S+),\s*amount=INR\s*([\d.]+),\s*date=(\S+),\s*narration="([^"]*)"'
        )
        candidates = cand_pattern.findall(user)
        for utr, b_amt_str, b_date, narration in candidates:
            if utr == "(blank)":
                continue
            delta = abs(float(b_amt_str) - ledger_amt)
            if (order_id and order_id in narration) or (ord_Fragment and ord_Fragment in narration):
                conf = round(max(0.74, 0.94 - min(delta, 2.0) * 0.06), 2)
                return json.dumps({
                    "proposed_match": utr,
                    "reasoning": (
                        f"Bank narration references {order_id} on {b_date} with "
                        f"rounding drift of INR {delta:.2f} (within ±INR 2.00 settlement band)."
                    ),
                    "confidence": conf,
                })

        if candidates:
            utr, b_amt_str, b_date, _ = candidates[0]
            delta = abs(float(b_amt_str) - ledger_amt)
            return json.dumps({
                "proposed_match": utr if utr != "(blank)" else None,
                "reasoning": (
                    f"Closest candidate {utr} differs by INR {delta:.2f} on {b_date} "
                    f"but carries no {order_id} reference in bank narration; likely orphan."
                ),
                "confidence": 0.34,
            })

        return json.dumps({
            "proposed_match": None,
            "reasoning": f"No settlement candidate within amount window for {order_id}; true orphan transaction.",
            "confidence": 0.08,
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
    3. LLM escalator (offline mock, heuristic agent, or live Ollama / OpenRouter call).
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
    delay_sec = (req.llm_delay_ms / 1000.0) if req.llm_mode in ("ollama", "openrouter") else 0.0
    cfg = EscalatorConfig(
        model=req.ollama_model if req.llm_mode == "ollama" else req.openrouter_model,
        confidence_threshold=req.confidence_threshold,
        call_delay_seconds=delay_sec,
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
    if req.llm_mode == "ollama":
        from src.matching.llm_escalator import OllamaLLMClient
        return OllamaLLMClient(
            model=req.ollama_model,
            api_key=req.ollama_api_key,
            base_url=req.ollama_host,
        )
    if req.llm_mode == "openrouter":
        key = req.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")
        if key:
            from src.matching.llm_escalator import OpenRouterLLMClient
            return OpenRouterLLMClient(model=req.openrouter_model, api_key=key)
    if req.llm_mode == "heuristic":
        return _HeuristicAgentClient()
    # Fallback: offline mock
    return _OfflineMockClient()
