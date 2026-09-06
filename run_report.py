"""
Run full pipeline on real data (data/*, seed=42, 300 rows) and print audit report.
Uses the deterministic matcher only (LLM stage marks all 33 unmatched as api_error
since no API key is configured in this run).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.matching.deterministic_matcher import DeterministicMatcher, load_bank, load_gateway, load_ledger
from src.matching.llm_escalator import LLMEscalator
from src.config import EscalatorConfig
from src.reporting.audit_report import ReportGenerator

DATA_DIR = Path("data")

# ---- Offline mock: mark all as unresolved (no API available) ----
class OfflineMock:
    def complete(self, system, user):
        import json
        return json.dumps({"proposed_match": None, "reasoning": "Offline — no API configured.", "confidence": 0.0})

ledger  = load_ledger(DATA_DIR / "internal_ledger.csv")
bank    = load_bank(DATA_DIR / "bank_settlement.csv")
gateway = load_gateway(DATA_DIR / "gateway_settlement.csv")

matcher = DeterministicMatcher()
rule_matches, unmatched = matcher.match(ledger, bank, gateway)

cfg = EscalatorConfig(confidence_threshold=0.70, call_delay_seconds=0.0)
llm_results = LLMEscalator(OfflineMock(), config=cfg).escalate(unmatched, bank, gateway)

gen = ReportGenerator(outputs_dir=Path("outputs"))
report = gen.generate(rule_matches + llm_results, DATA_DIR / "answer_key.csv")
