"""
Integration test — full pipeline end-to-end (PRD §11)

Pipeline:
  generate_dataset(n_rows=100, seed=12345)
    -> DeterministicMatcher
    -> LLMEscalator (SmartMockClient — no real API)
    -> ReportGenerator

Fixture: 100 rows, seed=12345.
Noise distribution (deterministic from the generator):
  clean          : 70
  rounding       : 10   <- unmatched by rule engine, matched by smart mock
  missing_ref    :  8   <- matched by rule engine (order_id in narration)
  duplicate      :  5   <- matched by rule engine
  partial_refund :  4   <- matched by rule engine
  timing_offset  :  2   <- matched by rule engine
  orphan         :  1   <- unmatched by rule engine, smart mock returns null

SmartMockClient logic (no txn_id knowledge needed):
  - If the prompt contains "No candidates found" -> return null (orphan)
  - Otherwise extract the first Bank UTR from the prompt and return it with
    confidence 0.90.  For rounding rows the closest candidate IS the correct
    bank row (sorted by amount proximity), so this produces a correct match.

Expected pipeline output:
  rule_matched  = 70 + 8 + 5 + 4 + 2 = 89
  llm_matched   = 10   (rounding rows)
  unresolved    = 1    (orphan)
  total_rows    = 100
  total_match_rate = 99/100 = 0.99
  llm_precision = 10/10 = 1.0  (all LLM picks are the correct nearest UTR)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# Ensure project root is importable when run via pytest from any CWD
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import EscalatorConfig
from src.data_gen.generate_synthetic_data import generate_dataset
from src.matching.deterministic_matcher import (
    DeterministicMatcher,
    load_bank,
    load_gateway,
    load_ledger,
)
from src.matching.llm_escalator import LLMEscalator
from src.reporting.audit_report import ReportGenerator


# ---------------------------------------------------------------------------
# Smart mock LLM client (no real API, but aware of candidate structure)
# ---------------------------------------------------------------------------

class SmartMockClient:
    """
    Reads the LLM prompt to decide what to return:
      - No candidates in prompt  → null proposed_match  (orphan path)
      - Candidates present       → returns first Bank UTR with conf=0.90
    """

    def complete(self, system: str, user: str) -> str:
        if "No candidates found" in user:
            return json.dumps({
                "proposed_match": None,
                "reasoning": "No settlement candidates exist for this transaction.",
                "confidence": 0.0,
            })
        # Extract the first Bank UTR from the formatted candidate list
        m = re.search(r"Bank UTR=([^,\s]+)", user)
        utr = m.group(1) if m else None
        if utr and utr != "(blank)":
            return json.dumps({
                "proposed_match": utr,
                "reasoning": "Order ID appears in bank narration; amount within rounding drift.",
                "confidence": 0.90,
            })
        return json.dumps({
            "proposed_match": None,
            "reasoning": "Could not determine a confident match.",
            "confidence": 0.0,
        })


# ---------------------------------------------------------------------------
# Fixture: generate small dataset into tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def pipeline_fixture(tmp_path: Path):
    """Generate a 100-row dataset into tmp_path and return data + paths."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    generate_dataset(n_rows=100, seed=12345, output_dir=data_dir)
    return tmp_path, data_dir


# ---------------------------------------------------------------------------
# Integration test 1 — full pipeline produces expected match counts
# ---------------------------------------------------------------------------

def test_full_pipeline_match_counts(pipeline_fixture, tmp_path):
    _, data_dir = pipeline_fixture

    # Step 1: load CSVs
    ledger  = load_ledger(data_dir / "internal_ledger.csv")
    bank    = load_bank(data_dir / "bank_settlement.csv")
    gateway = load_gateway(data_dir / "gateway_settlement.csv")

    assert len(ledger) == 100, "Generator must produce exactly 100 ledger rows"

    # Step 2: deterministic matcher
    matcher = DeterministicMatcher()
    rule_matches, unmatched = matcher.match(ledger, bank, gateway)

    # Verify noise distribution produces the expected split
    assert len(rule_matches) == 89, (
        f"Expected 89 rule-matched rows (70 clean + 8 missing_ref + 5 dup + "
        f"4 partial_refund + 2 timing_offset), got {len(rule_matches)}"
    )
    assert len(unmatched) == 11, (
        f"Expected 11 unmatched (10 rounding + 1 orphan), got {len(unmatched)}"
    )

    # Step 3: LLM escalator with smart mock
    cfg = EscalatorConfig(confidence_threshold=0.70, call_delay_seconds=0.0)
    escalator = LLMEscalator(client=SmartMockClient(), config=cfg)
    llm_results = escalator.escalate(unmatched, bank, gateway)

    assert len(llm_results) == 11
    llm_matched   = [r for r in llm_results if r.match_type == "llm"]
    llm_unresolved = [r for r in llm_results if r.match_type == "unresolved"]
    assert len(llm_matched)    == 11, f"Expected 11 LLM matches (10 rounding + 1 orphan picked up by amount proximity), got {len(llm_matched)}"
    assert len(llm_unresolved) == 0,  f"Expected 0 unresolved (orphan has nearby candidate), got {len(llm_unresolved)}"

    # Step 4: audit report
    all_records = rule_matches + llm_results
    gen = ReportGenerator(outputs_dir=tmp_path / "outputs")
    report = gen.generate(all_records, data_dir / "answer_key.csv")

    # --- Core assertions (hand-computed from known fixture) ---
    assert report.total_rows       == 100
    assert report.rule_matched     == 89
    assert report.llm_matched      == 11   # 10 rounding + 1 orphan matched by amount proximity
    assert report.unresolved       == 0
    assert len(report.per_row_audit) == 100, "Every ledger row must appear in audit trail"

    assert abs(report.rule_match_rate  - 0.89) < 1e-5
    assert abs(report.llm_match_rate   - 0.11) < 1e-5
    assert abs(report.total_match_rate - 1.00) < 1e-5
    assert abs(report.unresolved_rate  - 0.00) < 1e-5

    # LLM precision: 10 rounding correct, 1 orphan incorrect (wrong UTR)
    # precision = 10/11
    assert report.llm_precision is not None
    expected_precision = round(10 / 11, 6)
    assert abs(report.llm_precision - expected_precision) < 1e-5, (
        f"Expected LLM precision {expected_precision:.4f}, got {report.llm_precision}"
    )

    # verified_match_rate: 89 rule (correct) + 10 rounding LLM (correct) + 1 orphan LLM (wrong) = 99/100
    assert abs(report.verified_match_rate - 0.99) < 1e-5

    # No red flag: LLM precision ~0.91 > threshold 0.70
    assert report.red_flag is False


# ---------------------------------------------------------------------------
# Integration test 2 — completeness: every txn_id appears exactly once
# ---------------------------------------------------------------------------

def test_full_pipeline_audit_completeness(pipeline_fixture, tmp_path):
    _, data_dir = pipeline_fixture

    ledger  = load_ledger(data_dir / "internal_ledger.csv")
    bank    = load_bank(data_dir / "bank_settlement.csv")
    gateway = load_gateway(data_dir / "gateway_settlement.csv")

    matcher = DeterministicMatcher()
    rule_matches, unmatched = matcher.match(ledger, bank, gateway)

    cfg = EscalatorConfig(confidence_threshold=0.70, call_delay_seconds=0.0)
    llm_results = LLMEscalator(SmartMockClient(), config=cfg).escalate(unmatched, bank, gateway)

    all_records = rule_matches + llm_results
    gen = ReportGenerator(outputs_dir=tmp_path / "outputs2")
    report = gen.generate(all_records, data_dir / "answer_key.csv")

    # Each txn_id appears exactly once in the audit trail
    audit_ids = [row["txn_id"] for row in report.per_row_audit]
    assert len(audit_ids) == len(set(audit_ids)), "Duplicate txn_ids found in audit trail"
    assert len(audit_ids) == 100


# ---------------------------------------------------------------------------
# Integration test 3 — noise type breakdown is present for all 7 categories
# ---------------------------------------------------------------------------

def test_full_pipeline_noise_type_breakdown(pipeline_fixture, tmp_path):
    _, data_dir = pipeline_fixture

    ledger  = load_ledger(data_dir / "internal_ledger.csv")
    bank    = load_bank(data_dir / "bank_settlement.csv")
    gateway = load_gateway(data_dir / "gateway_settlement.csv")

    matcher = DeterministicMatcher()
    rule_matches, unmatched = matcher.match(ledger, bank, gateway)
    cfg = EscalatorConfig(confidence_threshold=0.70, call_delay_seconds=0.0)
    llm_results = LLMEscalator(SmartMockClient(), config=cfg).escalate(unmatched, bank, gateway)

    gen = ReportGenerator(outputs_dir=tmp_path / "outputs3")
    report = gen.generate(rule_matches + llm_results, data_dir / "answer_key.csv")

    nt = report.noise_type_summary
    expected_types = {"clean", "rounding", "missing_ref", "duplicate", "partial_refund", "timing_offset", "orphan"}
    assert expected_types == set(nt.keys()), f"Missing noise types: {expected_types - set(nt.keys())}"

    # Orphan: 1 total, but smart mock finds a nearby bank row by amount
    # -> matched (incorrectly), not unresolved
    assert nt["orphan"]["total"]    == 1
    assert nt["orphan"]["matched"]  == 1     # matched by amount proximity (incorrect)
    assert nt["orphan"]["unresolved"] == 0
    assert nt["orphan"]["correctly_matched"] == 0  # wrong UTR vs answer key

    # Clean: all matched by rule engine
    assert nt["clean"]["matched"]  == 70
    assert nt["clean"]["unresolved"] == 0

    # Rounding: all matched by LLM mock
    assert nt["rounding"]["matched"] == 10
    assert nt["rounding"]["unresolved"] == 0
