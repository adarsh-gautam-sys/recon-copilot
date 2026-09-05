"""
Tests for src/data_gen/generate_synthetic_data.py

Covers:
  1. Determinism: same seed → byte-identical CSV output across two runs.
  2. Noise distribution: 1 000 rows, each category within ±3 % of target.
  3. Completeness: every ledger txn_id has exactly one answer_key row.
  4. Orphan integrity: orphan rows have no matching bank or gateway entries.
  5. Partial-refund sums: the two bank amounts must sum exactly to ledger amount.
"""

from __future__ import annotations

import csv
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

import pytest

# Make the project root importable when running pytest from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_gen.generate_synthetic_data import generate_dataset, NOISE_WEIGHTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Test 1 — Determinism
# ---------------------------------------------------------------------------

def test_determinism(tmp_path: Path) -> None:
    """Same seed must produce byte-identical CSVs on two separate runs."""
    dir_a = tmp_path / "run_a"
    dir_b = tmp_path / "run_b"

    generate_dataset(n_rows=300, seed=42, output_dir=dir_a)
    generate_dataset(n_rows=300, seed=42, output_dir=dir_b)

    for fname in (
        "internal_ledger.csv",
        "bank_settlement.csv",
        "gateway_settlement.csv",
        "answer_key.csv",
    ):
        assert _md5(dir_a / fname) == _md5(dir_b / fname), (
            f"{fname}: runs with identical seeds produced different output."
        )


# ---------------------------------------------------------------------------
# Test 2 — Noise distribution (1 000 rows, ±3 %)
# ---------------------------------------------------------------------------

TARGET_PCTS: dict[str, float] = NOISE_WEIGHTS  # alias for readability

TOLERANCE = 0.03  # ±3 percentage points


def test_noise_distribution(tmp_path: Path) -> None:
    """With 1 000 rows, each noise category must be within ±3 % of its target."""
    n = 1000
    generate_dataset(n_rows=n, seed=99, output_dir=tmp_path)

    key_rows = _load_csv(tmp_path / "answer_key.csv")
    assert len(key_rows) == n, "answer_key row count must equal n_rows."

    counts: dict[str, int] = {}
    for row in key_rows:
        nt = row["noise_type"]
        counts[nt] = counts.get(nt, 0) + 1

    for category, target_pct in TARGET_PCTS.items():
        actual_pct = counts.get(category, 0) / n
        lo = target_pct - TOLERANCE
        hi = target_pct + TOLERANCE
        assert lo <= actual_pct <= hi, (
            f"Category '{category}': target={target_pct:.0%}, "
            f"actual={actual_pct:.1%} — outside ±{TOLERANCE:.0%} tolerance."
        )


# ---------------------------------------------------------------------------
# Test 3 — Completeness: every ledger row appears in answer_key
# ---------------------------------------------------------------------------

def test_answer_key_completeness(tmp_path: Path) -> None:
    """Every txn_id in internal_ledger.csv must have exactly one row in answer_key.csv."""
    generate_dataset(n_rows=300, seed=42, output_dir=tmp_path)

    ledger_ids = {r["txn_id"] for r in _load_csv(tmp_path / "internal_ledger.csv")}
    key_ids = [r["txn_id"] for r in _load_csv(tmp_path / "answer_key.csv")]

    # No orphaned ledger rows in the key
    assert set(key_ids) == ledger_ids, (
        "Mismatch between ledger txn_ids and answer_key txn_ids."
    )
    # No duplicate entries in the key
    assert len(key_ids) == len(set(key_ids)), (
        "answer_key.csv contains duplicate txn_id entries."
    )


# ---------------------------------------------------------------------------
# Test 4 — Orphan integrity
# ---------------------------------------------------------------------------

def test_orphan_has_no_settlement_rows(tmp_path: Path) -> None:
    """
    For every orphan row, no bank or gateway entry must exist that could
    plausibly match it (verified via narration order_id and UTR patterns).
    """
    generate_dataset(n_rows=300, seed=42, output_dir=tmp_path)

    key_rows = _load_csv(tmp_path / "answer_key.csv")
    ledger_rows = _load_csv(tmp_path / "internal_ledger.csv")
    bank_rows = _load_csv(tmp_path / "bank_settlement.csv")
    gateway_rows = _load_csv(tmp_path / "gateway_settlement.csv")

    # Build lookup: txn_id → order_id
    order_id_of: dict[str, str] = {r["txn_id"]: r["order_id"] for r in ledger_rows}

    # Collect all UTRs and narrations present in settlements
    bank_utrs: set[str] = {r["utr"] for r in bank_rows if r["utr"]}
    gateway_utrs: set[str] = {r["utr"] for r in gateway_rows if r["utr"]}
    gateway_payment_ids: set[str] = {r["payment_id"] for r in gateway_rows}
    bank_narrations: list[str] = [r["narration"] for r in bank_rows]

    orphan_txn_ids = [r["txn_id"] for r in key_rows if r["noise_type"] == "orphan"]
    assert orphan_txn_ids, "Test requires at least one orphan row (check seed/n_rows)."

    for txn_id in orphan_txn_ids:
        idx_str = txn_id.split("_")[1]   # e.g. "00042" from "TXN_00042"
        expected_utr = f"UTR_{idx_str}"
        expected_pay = f"PAY_{idx_str}"
        order_id = order_id_of[txn_id]

        # No bank UTR for this transaction
        assert expected_utr not in bank_utrs, (
            f"Orphan {txn_id}: found {expected_utr} in bank_settlement UTRs."
        )
        # No gateway UTR for this transaction
        assert expected_utr not in gateway_utrs, (
            f"Orphan {txn_id}: found {expected_utr} in gateway_settlement UTRs."
        )
        # No gateway payment_id for this transaction
        assert expected_pay not in gateway_payment_ids, (
            f"Orphan {txn_id}: found {expected_pay} in gateway payment_ids."
        )
        # order_id must not appear in any bank narration
        for narration in bank_narrations:
            assert order_id not in narration, (
                f"Orphan {txn_id}: order_id '{order_id}' found in bank narration '{narration}'."
            )


# ---------------------------------------------------------------------------
# Test 5 — Partial-refund amounts sum to ledger amount
# ---------------------------------------------------------------------------

def test_partial_refund_sums(tmp_path: Path) -> None:
    """
    For each partial_refund row, the two bank settlement amounts must sum
    exactly to the ledger amount (using Decimal to avoid float drift).
    """
    generate_dataset(n_rows=300, seed=42, output_dir=tmp_path)

    key_rows = _load_csv(tmp_path / "answer_key.csv")
    ledger_rows = _load_csv(tmp_path / "internal_ledger.csv")
    bank_rows = _load_csv(tmp_path / "bank_settlement.csv")

    # Build lookups
    ledger_amount_of: dict[str, Decimal] = {
        r["txn_id"]: Decimal(r["amount"]) for r in ledger_rows
    }
    bank_by_utr: dict[str, Decimal] = {
        r["utr"]: Decimal(r["amount"]) for r in bank_rows if r["utr"]
    }

    pr_rows = [r for r in key_rows if r["noise_type"] == "partial_refund"]
    assert pr_rows, "Test requires at least one partial_refund row."

    for row in pr_rows:
        txn_id = row["txn_id"]
        match_str = row["ground_truth_match"]
        utrs = match_str.split("|")
        assert len(utrs) == 2, (
            f"{txn_id}: expected 2 pipe-separated UTRs in ground_truth_match, "
            f"got: '{match_str}'"
        )

        utr_a, utr_b = utrs
        assert utr_a in bank_by_utr, f"{txn_id}: UTR '{utr_a}' not found in bank_settlement."
        assert utr_b in bank_by_utr, f"{txn_id}: UTR '{utr_b}' not found in bank_settlement."

        bank_sum = bank_by_utr[utr_a] + bank_by_utr[utr_b]
        ledger_amount = ledger_amount_of[txn_id]

        assert bank_sum == ledger_amount, (
            f"{txn_id}: bank sum {bank_sum} != ledger amount {ledger_amount}."
        )
