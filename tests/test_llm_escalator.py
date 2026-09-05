"""
Tests for src/matching/llm_escalator.py

All LLM calls are mocked — no real API is ever contacted.

Five test cases:
  1. High-confidence, well-formed response   → match_type="llm"
  2. Low-confidence response (0.4)           → forced to "unresolved" (hard gate)
  3. null proposed_match from LLM            → "unresolved", no error
  4. Malformed / invalid JSON response       → "unresolved", no crash
  5. API timeout / exception                 → "unresolved", pipeline doesn't crash
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import EscalatorConfig
from src.matching.deterministic_matcher import BankRecord, GatewayRecord, LedgerRecord
from src.matching.llm_escalator import LLMEscalator


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2024, 6, 1, 10, 0, 0)


def _ledger(
    txn_id: str = "TXN_00042",
    order_id: str = "ORD_00042",
    amount: str = "1632.49",
) -> LedgerRecord:
    return LedgerRecord(
        txn_id=txn_id,
        order_id=order_id,
        amount=Decimal(amount),
        currency="INR",
        timestamp=_BASE_TS,
        status="captured",
    )


def _bank(
    row_index: int = 0,
    utr: str = "UTR_00042",
    amount: str = "1633.72",   # rounding drift — won't match deterministically
    order_id: str = "ORD_00042",
) -> BankRecord:
    from datetime import date
    return BankRecord(
        row_index=row_index,
        utr=utr,
        amount=Decimal(amount),
        value_date=date(2024, 6, 1),
        narration=f"NEFT/{order_id}/AXIS",
    )


def _gateway(
    payment_id: str = "PAY_00042",
    utr: str = "UTR_00042",
    amount: str = "1600.06",
    fee: str = "33.66",
) -> GatewayRecord:
    return GatewayRecord(
        payment_id=payment_id,
        utr=utr,
        amount=Decimal(amount),
        fee=Decimal(fee),
        settled_at=_BASE_TS,
    )


def _make_escalator(response_text: str) -> LLMEscalator:
    """Build an escalator backed by a mock client returning *response_text*."""
    class _MockClient:
        def complete(self, system: str, user: str) -> str:
            return response_text

    return LLMEscalator(client=_MockClient(), config=EscalatorConfig(confidence_threshold=0.70))


# ---------------------------------------------------------------------------
# Test 1 — High confidence, well-formed → match_type "llm"
# ---------------------------------------------------------------------------

def test_high_confidence_well_formed_becomes_llm_match() -> None:
    payload = json.dumps({
        "proposed_match": "UTR_00042",
        "reasoning": "Order ID matches narration and amount is within rounding drift.",
        "confidence": 0.92,
    })
    escalator = _make_escalator(payload)

    results = escalator.escalate(
        unmatched=[_ledger()],
        bank=[_bank()],
        gateway=[_gateway()],
    )

    assert len(results) == 1
    m = results[0]
    assert m.match_type == "llm", f"Expected 'llm', got '{m.match_type}'"
    assert m.confidence == 0.92
    assert m.matched_bank_utr == "UTR_00042"
    assert m.matched_gateway_payment_id == "PAY_00042"
    assert m.reasoning is not None and len(m.reasoning) > 0
    assert m.exception_category is None
    assert m.ledger_txn_id == "TXN_00042"


# ---------------------------------------------------------------------------
# Test 2 — Low confidence (0.4) → hard gate forces "unresolved"
# ---------------------------------------------------------------------------

def test_low_confidence_forced_to_unresolved() -> None:
    payload = json.dumps({
        "proposed_match": "UTR_00042",
        "reasoning": "Weak amount proximity match; reference unclear.",
        "confidence": 0.40,   # below 0.70 threshold
    })
    escalator = _make_escalator(payload)

    results = escalator.escalate(
        unmatched=[_ledger()],
        bank=[_bank()],
        gateway=[_gateway()],
    )

    assert len(results) == 1
    m = results[0]
    assert m.match_type == "unresolved", (
        f"Low confidence (0.40) must be forced to 'unresolved', got '{m.match_type}'"
    )
    assert m.exception_category == "low_confidence"
    # The LLM confidence should still be recorded (for threshold justification)
    assert m.confidence == 0.40


# ---------------------------------------------------------------------------
# Test 3 — null proposed_match → "unresolved" without error
# ---------------------------------------------------------------------------

def test_null_proposed_match_becomes_unresolved_gracefully() -> None:
    payload = json.dumps({
        "proposed_match": None,
        "reasoning": "No candidate has a matching reference or close enough amount.",
        "confidence": 0.0,
    })
    escalator = _make_escalator(payload)

    results = escalator.escalate(
        unmatched=[_ledger()],
        bank=[_bank()],
        gateway=[_gateway()],
    )

    assert len(results) == 1
    m = results[0]
    assert m.match_type == "unresolved"
    assert m.exception_category == "no_llm_match"
    assert m.matched_bank_utr is None
    assert m.matched_gateway_payment_id is None


# ---------------------------------------------------------------------------
# Test 4 — Malformed JSON → "unresolved", no crash, response logged
# ---------------------------------------------------------------------------

def test_malformed_json_handled_as_unresolved_no_crash() -> None:
    malformed_text = "INVALID JSON {{{ broken ]]"
    escalator = _make_escalator(malformed_text)

    # Must not raise — must return unresolved gracefully
    results = escalator.escalate(
        unmatched=[_ledger()],
        bank=[_bank()],
        gateway=[_gateway()],
    )

    assert len(results) == 1
    m = results[0]
    assert m.match_type == "unresolved"
    assert m.exception_category == "malformed_response"
    assert m.matched_bank_utr is None


def test_missing_field_json_handled_as_unresolved() -> None:
    """JSON with valid syntax but missing required fields → malformed_response."""
    partial_payload = json.dumps({"proposed_match": "UTR_00042"})  # missing reasoning + confidence
    escalator = _make_escalator(partial_payload)

    results = escalator.escalate(
        unmatched=[_ledger()], bank=[_bank()], gateway=[_gateway()]
    )

    assert results[0].match_type == "unresolved"
    assert results[0].exception_category == "malformed_response"


# ---------------------------------------------------------------------------
# Test 5 — API exception → "unresolved", pipeline doesn't crash
# ---------------------------------------------------------------------------

def test_api_exception_caught_pipeline_continues() -> None:
    """Simulates a network / API timeout; the row must become unresolved."""

    class _TimeoutClient:
        def complete(self, system: str, user: str) -> str:
            raise ConnectionError("Connection timed out after 30s")

    escalator = LLMEscalator(
        client=_TimeoutClient(),
        config=EscalatorConfig(confidence_threshold=0.70),
    )

    # Must not propagate the exception
    results = escalator.escalate(
        unmatched=[_ledger()],
        bank=[_bank()],
        gateway=[_gateway()],
    )

    assert len(results) == 1
    m = results[0]
    assert m.match_type == "unresolved"
    assert m.exception_category == "api_error"
    # Pipeline must also continue past the failing row
    # (add a second row to prove it doesn't short-circuit the loop)
    results2 = escalator.escalate(
        unmatched=[_ledger("TXN_00001"), _ledger("TXN_00002")],
        bank=[_bank()],
        gateway=[_gateway()],
    )
    assert len(results2) == 2
    assert all(r.match_type == "unresolved" for r in results2)
