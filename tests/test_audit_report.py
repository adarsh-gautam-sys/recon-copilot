"""
Unit tests for src/reporting/audit_report.py

All tests use hand-built Match Record + answer_key fixtures.
No real data files or LLM API calls involved.

Coverage:
  1. Missing row in match_records raises ValueError
  2. Duplicate row in match_records raises ValueError
  3. Match rate arithmetic against a known 5-row fixture
  4. LLM precision computed correctly when some LLM matches are wrong
  5. Exception categorisation cross-references noise_type from answer key
  6. Edge case: all-unresolved (0 matches) — no crash, llm_precision=None
  7. Edge case: all-matched (0 exceptions) — no crash, empty breakdown
"""

from __future__ import annotations

import csv
import io
import textwrap
from pathlib import Path

import pytest

from src.matching.deterministic_matcher import MatchRecord
from src.reporting.audit_report import AuditReport, AuditRow, ReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_record(
    txn_id: str,
    match_type: str = "rule",
    bank_utr: str | None = None,
    gw_pay: str | None = None,
    confidence: float = 1.0,
    reasoning: str | None = None,
    exception_category: str | None = None,
) -> MatchRecord:
    return MatchRecord(
        match_id=f"{match_type.upper()}_{txn_id}",
        ledger_txn_id=txn_id,
        matched_bank_utr=bank_utr,
        matched_gateway_payment_id=gw_pay,
        match_type=match_type,
        confidence=confidence,
        reasoning=reasoning,
        exception_category=exception_category,
    )


def _write_answer_key(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a minimal answer_key.csv and return its path."""
    p = tmp_path / "answer_key.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["txn_id", "ground_truth_match", "noise_type"])
        writer.writeheader()
        writer.writerows(rows)
    return p


def _gen(tmp_path: Path, records: list[MatchRecord], ak_rows: list[dict]) -> AuditReport:
    """Helper: write answer_key CSV, generate report, return AuditReport."""
    ak_path = _write_answer_key(tmp_path, ak_rows)
    gen = ReportGenerator(outputs_dir=tmp_path / "outputs")
    return gen.generate(records, ak_path)


# ---------------------------------------------------------------------------
# Test 1 — Missing row raises ValueError
# ---------------------------------------------------------------------------

def test_missing_row_raises(tmp_path: Path) -> None:
    ak = [
        {"txn_id": "A", "ground_truth_match": "UTR_A", "noise_type": "clean"},
        {"txn_id": "B", "ground_truth_match": "UTR_B", "noise_type": "clean"},
        {"txn_id": "C", "ground_truth_match": "UTR_C", "noise_type": "clean"},
    ]
    # Only provide records for A and B — C is missing
    records = [
        _mk_record("A", bank_utr="UTR_A"),
        _mk_record("B", bank_utr="UTR_B"),
    ]
    ak_path = _write_answer_key(tmp_path, ak)
    gen = ReportGenerator(outputs_dir=tmp_path / "outputs")
    with pytest.raises(ValueError, match="Missing txn_ids"):
        gen.generate(records, ak_path)


# ---------------------------------------------------------------------------
# Test 2 — Duplicate row raises ValueError
# ---------------------------------------------------------------------------

def test_duplicate_row_raises(tmp_path: Path) -> None:
    ak = [
        {"txn_id": "A", "ground_truth_match": "UTR_A", "noise_type": "clean"},
        {"txn_id": "B", "ground_truth_match": "UTR_B", "noise_type": "clean"},
    ]
    records = [
        _mk_record("A", bank_utr="UTR_A"),
        _mk_record("A", bank_utr="UTR_A"),   # duplicate
        _mk_record("B", bank_utr="UTR_B"),
    ]
    ak_path = _write_answer_key(tmp_path, ak)
    gen = ReportGenerator(outputs_dir=tmp_path / "outputs")
    with pytest.raises(ValueError, match="Duplicate txn_ids"):
        gen.generate(records, ak_path)


# ---------------------------------------------------------------------------
# Test 3 — Match rate arithmetic (5-row known fixture)
# ---------------------------------------------------------------------------
#
# Fixture:
#   A — rule match, correct UTR     -> rule-matched, correct
#   B — rule match, correct UTR     -> rule-matched, correct
#   C — rule match, correct UTR     -> rule-matched, correct
#   D — llm match, confidence 0.9   -> llm-matched, correct
#   E — unresolved (orphan)         -> unresolved, correct (no GT)
#
# Expected:
#   rule_match_rate = 3/5 = 0.6
#   llm_match_rate  = 1/5 = 0.2
#   total_match_rate= 4/5 = 0.8
#   unresolved_rate = 1/5 = 0.2
#   llm_precision   = 1/1 = 1.0
#   verified_match_rate = 5/5 = 1.0  (3 correct rule + 1 correct llm + 1 correct unresolved)

def test_match_rate_math(tmp_path: Path) -> None:
    ak = [
        {"txn_id": "A", "ground_truth_match": "UTR_A", "noise_type": "clean"},
        {"txn_id": "B", "ground_truth_match": "UTR_B", "noise_type": "clean"},
        {"txn_id": "C", "ground_truth_match": "UTR_C", "noise_type": "clean"},
        {"txn_id": "D", "ground_truth_match": "UTR_D", "noise_type": "rounding"},
        {"txn_id": "E", "ground_truth_match": "",       "noise_type": "orphan"},
    ]
    records = [
        _mk_record("A", "rule", bank_utr="UTR_A"),
        _mk_record("B", "rule", bank_utr="UTR_B"),
        _mk_record("C", "rule", bank_utr="UTR_C"),
        _mk_record("D", "llm",  bank_utr="UTR_D", confidence=0.9,
                   reasoning="Order ID in narration, amount within drift."),
        _mk_record("E", "unresolved", exception_category="no_llm_match"),
    ]
    r = _gen(tmp_path, records, ak)

    assert r.total_rows == 5
    assert r.rule_matched == 3
    assert r.llm_matched == 1
    assert r.unresolved == 1
    assert abs(r.rule_match_rate  - 3/5) < 1e-6
    assert abs(r.llm_match_rate   - 1/5) < 1e-6
    assert abs(r.total_match_rate - 4/5) < 1e-6
    assert abs(r.unresolved_rate  - 1/5) < 1e-6
    assert r.llm_precision == 1.0
    assert abs(r.verified_match_rate - 5/5) < 1e-6


# ---------------------------------------------------------------------------
# Test 4 — LLM precision with some wrong matches
# ---------------------------------------------------------------------------
#
# Fixture:
#   F — llm match, UTR_F in GT  -> correct    (1 right)
#   G — llm match, UTR_WRONG    -> INCORRECT  (1 wrong, GT is UTR_G)
#   H — rule match, UTR_H in GT -> correct (rule, not counted in llm_precision)
#
# LLM precision = 1 correct / 2 llm = 0.5

def test_llm_precision_partial_wrong(tmp_path: Path) -> None:
    ak = [
        {"txn_id": "F", "ground_truth_match": "UTR_F",   "noise_type": "rounding"},
        {"txn_id": "G", "ground_truth_match": "UTR_G",   "noise_type": "rounding"},
        {"txn_id": "H", "ground_truth_match": "UTR_H",   "noise_type": "clean"},
    ]
    records = [
        _mk_record("F", "llm",  bank_utr="UTR_F",   confidence=0.9),
        _mk_record("G", "llm",  bank_utr="UTR_WRONG", confidence=0.8),  # wrong
        _mk_record("H", "rule", bank_utr="UTR_H"),
    ]
    r = _gen(tmp_path, records, ak)

    assert r.llm_matched == 2
    assert r.llm_precision is not None
    assert abs(r.llm_precision - 0.5) < 1e-6, f"Expected 0.5, got {r.llm_precision}"
    # Rule precision is not tracked separately but verified_match_rate should reflect correctness
    # Correct: F(llm, right) + H(rule, right) = 2 correct matches + G (llm, wrong) = 1 incorrect
    # Unresolved: 0.  Correct count = 2.
    assert abs(r.verified_match_rate - 2/3) < 1e-6


# ---------------------------------------------------------------------------
# Test 5 — Exception categorisation cross-references noise_type
# ---------------------------------------------------------------------------

def test_exception_categorisation_cross_references_noise_type(tmp_path: Path) -> None:
    ak = [
        {"txn_id": "P", "ground_truth_match": "",      "noise_type": "orphan"},
        {"txn_id": "Q", "ground_truth_match": "UTR_Q", "noise_type": "rounding"},
        {"txn_id": "R", "ground_truth_match": "UTR_R", "noise_type": "rounding"},
    ]
    records = [
        _mk_record("P", "unresolved", exception_category="no_llm_match"),
        _mk_record("Q", "unresolved", exception_category="api_error"),
        _mk_record("R", "unresolved", exception_category="low_confidence"),
    ]
    r = _gen(tmp_path, records, ak)

    assert r.exception_breakdown == {
        "no_llm_match": 1,
        "api_error": 1,
        "low_confidence": 1,
    }
    # Noise type summary: orphan has 1 unresolved (matches GT), rounding has 2 unresolved (doesn't)
    assert r.noise_type_summary["orphan"]["unresolved"] == 1
    assert r.noise_type_summary["rounding"]["unresolved"] == 2

    # P is correctly unresolved (orphan with no GT) -> matches_ground_truth=True
    # Q, R are unresolved but had a GT match -> matches_ground_truth=False
    audit_map = {row["txn_id"]: row for row in r.per_row_audit}
    assert audit_map["P"]["matches_ground_truth"] is True
    assert audit_map["Q"]["matches_ground_truth"] is False
    assert audit_map["R"]["matches_ground_truth"] is False


# ---------------------------------------------------------------------------
# Test 6 — All-unresolved edge case (zero matches, zero LLM)
# ---------------------------------------------------------------------------

def test_all_unresolved_no_crash(tmp_path: Path) -> None:
    ak = [
        {"txn_id": "X", "ground_truth_match": "UTR_X", "noise_type": "rounding"},
        {"txn_id": "Y", "ground_truth_match": "UTR_Y", "noise_type": "rounding"},
    ]
    records = [
        _mk_record("X", "unresolved", exception_category="api_error"),
        _mk_record("Y", "unresolved", exception_category="api_error"),
    ]
    r = _gen(tmp_path, records, ak)

    assert r.rule_matched == 0
    assert r.llm_matched == 0
    assert r.unresolved == 2
    assert r.rule_match_rate == 0.0
    assert r.llm_match_rate == 0.0
    assert r.total_match_rate == 0.0
    assert r.llm_precision is None, "llm_precision must be None when no LLM matches exist"
    assert r.red_flag is False


# ---------------------------------------------------------------------------
# Test 7 — All-matched edge case (zero exceptions)
# ---------------------------------------------------------------------------

def test_all_matched_no_crash(tmp_path: Path) -> None:
    ak = [
        {"txn_id": "M", "ground_truth_match": "UTR_M", "noise_type": "clean"},
        {"txn_id": "N", "ground_truth_match": "UTR_N", "noise_type": "clean"},
        {"txn_id": "O", "ground_truth_match": "UTR_O", "noise_type": "clean"},
    ]
    records = [
        _mk_record("M", "rule", bank_utr="UTR_M"),
        _mk_record("N", "rule", bank_utr="UTR_N"),
        _mk_record("O", "rule", bank_utr="UTR_O"),
    ]
    r = _gen(tmp_path, records, ak)

    assert r.unresolved == 0
    assert r.exception_breakdown == {}
    assert r.unresolved_rate == 0.0
    assert r.total_match_rate == 1.0
    assert r.llm_precision is None  # no LLM matches
    assert len(r.per_row_audit) == 3


# ---------------------------------------------------------------------------
# Test 8 — per_row_audit contains every row, correct fields populated
# ---------------------------------------------------------------------------

def test_per_row_audit_completeness(tmp_path: Path) -> None:
    ak = [
        {"txn_id": "T1", "ground_truth_match": "UTR_T1", "noise_type": "clean"},
        {"txn_id": "T2", "ground_truth_match": "UTR_T2", "noise_type": "rounding"},
    ]
    records = [
        _mk_record("T1", "rule", bank_utr="UTR_T1"),
        _mk_record("T2", "llm",  bank_utr="UTR_T2", confidence=0.88,
                   reasoning="Amount within rounding drift; order ID in narration."),
    ]
    r = _gen(tmp_path, records, ak)

    assert len(r.per_row_audit) == 2
    by_id = {row["txn_id"]: row for row in r.per_row_audit}

    t1 = by_id["T1"]
    assert t1["match_type"] == "rule"
    assert t1["engine"] == "rule"
    assert t1["matched_bank_utr"] == "UTR_T1"
    assert t1["noise_type"] == "clean"
    assert t1["matches_ground_truth"] is True
    assert t1["confidence"] == 1.0

    t2 = by_id["T2"]
    assert t2["match_type"] == "llm"
    assert t2["confidence"] == 0.88
    assert "rounding drift" in (t2["reasoning"] or "")
    assert t2["matches_ground_truth"] is True


# ---------------------------------------------------------------------------
# Test 9 — JSON file is written to outputs_dir
# ---------------------------------------------------------------------------

def test_json_file_written(tmp_path: Path) -> None:
    ak = [{"txn_id": "Z", "ground_truth_match": "UTR_Z", "noise_type": "clean"}]
    records = [_mk_record("Z", "rule", bank_utr="UTR_Z")]
    r = _gen(tmp_path, records, ak)

    out_dir = tmp_path / "outputs"
    json_files = list(out_dir.glob("report_*.json"))
    assert len(json_files) == 1, "Expected exactly one JSON report file"

    import json
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert data["total_rows"] == 1
    assert data["rule_matched"] == 1
