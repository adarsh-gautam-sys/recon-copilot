"""
Centralized configuration for Reconciliation Copilot.

All tuneable knobs live here so they can be passed around as typed objects
rather than scattered magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


@dataclass
class MatcherConfig:
    """Configuration for the deterministic rule-based matcher (PRD §7.2)."""
    amount_tol: Decimal = Decimal("0.01")   # ±₹0.01 amount tolerance
    date_window_days: int = 3               # settlement date window (days)


@dataclass
class EscalatorConfig:
    """Configuration for the LLM escalation layer (PRD §7.3)."""
    model: str = "claude-3-5-haiku-20241022"
    confidence_threshold: float = 0.70      # hard gate — below this → unresolved
    top_n_candidates: int = 5               # max candidates sent to LLM per row
    candidate_window_pct: float = 0.10      # ±10 % of ledger amount for candidates
    max_tokens: int = 512                   # max LLM response tokens
    timeout_seconds: int = 30              # API call timeout
    call_delay_seconds: float = 0.0        # inter-call delay (use ~2.0 for free-tier APIs)


@dataclass
class PipelineConfig:
    """Top-level pipeline configuration."""
    data_dir: Path = field(default_factory=lambda: Path("data"))
    outputs_dir: Path = field(default_factory=lambda: Path("outputs"))
    matcher: MatcherConfig = field(default_factory=MatcherConfig)
    escalator: EscalatorConfig = field(default_factory=EscalatorConfig)
