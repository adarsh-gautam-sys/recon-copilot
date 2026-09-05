"""
Tests for src/matching/deterministic_matcher.py

All fixtures are hand-built — no dependency on the synthetic data generator.
Six test cases covering the full matching surface:
  1. Exact match → confidence 1.0, match_type "rule"
  2. Rounding tolerance (within ±₹0.01 passes; ₹5 gap fails)
  3. Date window boundary (exactly 3 days passes; 4 days fails)
  4. No false positive (same amount but different order_id → no match)
  5. Partial refund (two bank rows summing to ledger amount → rule match)
  6. Orphan (no candidate anywhere → lands in unmatched list)
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.matching.deterministic_matcher import (
    BankRecord,
    DeterministicMatcher,
    GatewayRecord,
    LedgerRecord,
    MatchRecord,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

_BASE_DATE = datetime(2024, 6, 1, 10, 0, 0)
_BASE_D = date(2024, 6, 1)


def _ledger(
    txn_id: str = "TXN_00001",
    order_id: str = "ORD_00001",
    amount: str = "1000.00",
    ts: datetime = _BASE_DATE,
) -> LedgerRecord:
    return LedgerRecord(
        txn_id=txn_id,
        order_id=order_id,
        amount=Decimal(amount),
        currency="INR",
        timestamp=ts,
        status="captured",
    )


def _bank(
    row_index: int = 0,
    utr: str = "UTR_00001",
    amount: str = "1000.00",
    value_date: date = _BASE_D,
    order_id: str = "ORD_00001",
) -> BankRecord:
    return BankRecord(
        row_index=row_index,
        utr=utr,
        amount=Decimal(amount),
        value_date=value_date,
        narration=f"NEFT/{order_id}/AXIS",
    )


def _gateway(
    payment_id: str = "PAY_00001",
    utr: str = "UTR_00001",
    amount: str = "980.00",
    fee: str = "20.00",
    settled_at: datetime = _BASE_DATE,
) -> GatewayRecord:
    return GatewayRecord(
        payment_id=payment_id,
        utr=utr,
        amount=Decimal(amount),
        fee=Decimal(fee),
        settled_at=settled_at,
    )


_MATCHER = DeterministicMatcher(amount_tol=Decimal("0.01"), date_window=3)


# ---------------------------------------------------------------------------
# Test 1 — Exact match
# ---------------------------------------------------------------------------

def test_exact_match() -> None:
    """Identical amount, same order_id in narration, same date → rule match at 1.0."""
    ledger = [_ledger()]
    bank = [_bank()]
    gateway = [_gateway()]

    matched, unmatched = _MATCHER.match(ledger, bank, gateway)

    assert len(matched) == 1
    assert len(unmatched) == 0
    m = matched[0]
    assert m.match_type == "rule"
    assert m.confidence == 1.0
    assert m.ledger_txn_id == "TXN_00001"
    assert m.matched_bank_utr == "UTR_00001"
    assert m.matched_gateway_payment_id == "PAY_00001"
    assert m.reasoning is None
    assert m.exception_category is None


# ---------------------------------------------------------------------------
# Test 2 — Rounding tolerance
# ---------------------------------------------------------------------------

def test_rounding_within_tolerance_matches() -> None:
    """Bank amount off by exactly ₹0.01 → still a rule match."""
    ledger = [_ledger(amount="1000.00")]
    bank = [_bank(amount="1000.01")]
    gateway = [_gateway(amount="980.00", fee="20.01")]

    matched, unmatched = _MATCHER.match(ledger, bank, gateway)
    assert len(matched) == 1 and len(unmatched) == 0
    assert matched[0].match_type == "rule"


def test_rounding_outside_tolerance_no_match() -> None:
    """Bank amount off by ₹5.00 → does NOT match (falls through to unmatched)."""
    ledger = [_ledger(amount="1000.00")]
    bank = [_bank(amount="1005.00")]
    gateway = [_gateway()]

    matched, unmatched = _MATCHER.match(ledger, bank, gateway)
    assert len(matched) == 0
    assert len(unmatched) == 1
    assert unmatched[0].txn_id == "TXN_00001"


# ---------------------------------------------------------------------------
# Test 3 — Date window boundary
# ---------------------------------------------------------------------------

def test_date_window_exact_boundary_matches() -> None:
    """Settlement exactly 3 days after ledger timestamp → still within window."""
    from datetime import timedelta

    ts = datetime(2024, 6, 1, 10, 0, 0)
    settlement_date = date(2024, 6, 4)   # exactly 3 days later

    ledger = [_ledger(ts=ts)]
    bank = [_bank(value_date=settlement_date)]
    gateway = [_gateway(settled_at=ts + timedelta(days=3))]

    matched, unmatched = _MATCHER.match(ledger, bank, gateway)
    assert len(matched) == 1 and len(unmatched) == 0


def test_date_window_one_past_boundary_no_match() -> None:
    """Settlement exactly 4 days after ledger timestamp → outside window → unmatched."""
    from datetime import timedelta

    ts = datetime(2024, 6, 1, 10, 0, 0)
    settlement_date = date(2024, 6, 5)   # 4 days later

    ledger = [_ledger(ts=ts)]
    bank = [_bank(value_date=settlement_date)]
    gateway = [_gateway(settled_at=ts + timedelta(days=4))]

    matched, unmatched = _MATCHER.match(ledger, bank, gateway)
    assert len(matched) == 0
    assert len(unmatched) == 1


# ---------------------------------------------------------------------------
# Test 4 — No false positive (same amount, different order_id)
# ---------------------------------------------------------------------------

def test_no_false_positive_same_amount_different_order_id() -> None:
    """
    Two ledger rows with the same amount but different order_ids.
    Bank row only belongs to order 2 (narration = NEFT/ORD_00002/AXIS).
    Ledger row 1 must NOT be matched to it.
    """
    ledger_1 = _ledger(txn_id="TXN_00001", order_id="ORD_00001", amount="1000.00")
    ledger_2 = _ledger(txn_id="TXN_00002", order_id="ORD_00002", amount="1000.00")

    bank_for_2 = _bank(row_index=0, utr="UTR_00002", amount="1000.00", order_id="ORD_00002")
    gateway_for_2 = _gateway(payment_id="PAY_00002", utr="UTR_00002")

    matched, unmatched = _MATCHER.match(
        [ledger_1, ledger_2], [bank_for_2], [gateway_for_2]
    )

    assert len(matched) == 1, "Only ledger row 2 should match."
    assert matched[0].ledger_txn_id == "TXN_00002"

    assert len(unmatched) == 1, "Ledger row 1 must be unmatched (different order_id)."
    assert unmatched[0].txn_id == "TXN_00001"


# ---------------------------------------------------------------------------
# Test 5 — Partial refund (two bank rows sum to ledger amount)
# ---------------------------------------------------------------------------

def test_partial_refund_two_rows_sum_to_ledger() -> None:
    """
    Ledger amount = ₹1000.00.
    Bank has two rows for the same order: ₹400.00 and ₹600.00.
    Neither alone satisfies the amount condition (tol=₹0.01),
    but together they sum to ₹1000.00 → rule match.
    """
    ledger = [_ledger(amount="1000.00")]
    bank = [
        _bank(row_index=0, utr="UTR_00001_A", amount="400.00"),
        _bank(row_index=1, utr="UTR_00001_B", amount="600.00"),
    ]
    gateway = [
        _gateway(payment_id="PAY_00001_A", utr="UTR_00001_A", amount="392.00", fee="8.00"),
        _gateway(payment_id="PAY_00001_B", utr="UTR_00001_B", amount="588.00", fee="12.00"),
    ]

    matched, unmatched = _MATCHER.match(ledger, bank, gateway)

    assert len(unmatched) == 0, "Partial-refund ledger row must NOT be left unmatched."
    assert len(matched) == 1

    m = matched[0]
    assert m.match_type == "rule"
    assert m.confidence == 1.0

    # Both UTRs must be present in the matched_bank_utr (pipe-separated)
    assert m.matched_bank_utr is not None
    recorded_utrs = set(m.matched_bank_utr.split("|"))
    assert "UTR_00001_A" in recorded_utrs
    assert "UTR_00001_B" in recorded_utrs

    # Both payment_ids must be present
    assert m.matched_gateway_payment_id is not None
    recorded_pays = set(m.matched_gateway_payment_id.split("|"))
    assert "PAY_00001_A" in recorded_pays
    assert "PAY_00001_B" in recorded_pays


# ---------------------------------------------------------------------------
# Test 6 — Orphan (no settlement candidate anywhere)
# ---------------------------------------------------------------------------

def test_orphan_ends_up_in_unmatched_not_force_matched() -> None:
    """
    An orphan ledger row (no bank/gateway entries at all) must land in
    the unmatched list, never in matched, even if other transactions exist
    with similar amounts in the bank.
    """
    # Orphan row — ₹5000, order ORD_00999
    orphan = _ledger(txn_id="TXN_00999", order_id="ORD_00999", amount="5000.00")

    # Unrelated bank row for a different order with the same amount
    unrelated_bank = _bank(
        row_index=0,
        utr="UTR_00001",
        amount="5000.00",
        order_id="ORD_00001",   # different order — narration won't contain ORD_00999
    )
    unrelated_gw = _gateway(payment_id="PAY_00001", utr="UTR_00001", amount="4900.00", fee="100.00")

    matched, unmatched = _MATCHER.match([orphan], [unrelated_bank], [unrelated_gw])

    assert len(matched) == 0, "Orphan row must NOT be force-matched to a different order."
    assert len(unmatched) == 1
    assert unmatched[0].txn_id == "TXN_00999"
