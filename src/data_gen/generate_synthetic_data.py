"""
Synthetic Data Generator for Reconciliation Copilot.

Produces four CSVs into a target directory:
  - internal_ledger.csv
  - bank_settlement.csv
  - gateway_settlement.csv
  - answer_key.csv

Noise distribution (PRD §7.1):
  70% clean | 10% rounding | 8% missing_ref | 5% duplicate |
  4% partial_refund | 2% timing_offset | 1% orphan

Usage:
  python -m src.data_gen.generate_synthetic_data [--rows 300] [--seed 42] [--out data/]
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass, field, fields, asdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOISE_WEIGHTS: dict[str, float] = {
    "clean": 0.70,
    "rounding": 0.10,
    "missing_ref": 0.08,
    "duplicate": 0.05,
    "partial_refund": 0.04,
    "timing_offset": 0.02,
    "orphan": 0.01,
}

BASE_DATE = datetime(2024, 1, 1, 9, 0, 0)
GATEWAY_FEE_RATE = Decimal("0.02")  # 2% gateway fee


# ---------------------------------------------------------------------------
# Row dataclasses  (schema per PRD §6)
# ---------------------------------------------------------------------------

@dataclass
class LedgerRow:
    txn_id: str
    order_id: str
    amount: str          # decimal string, always 2 dp
    currency: str
    timestamp: str       # ISO datetime
    status: str          # captured | refunded | partial_refund


@dataclass
class BankRow:
    utr: str             # may be blank for missing_ref
    amount: str          # decimal string, 2 dp; may have rounding drift
    value_date: str      # YYYY-MM-DD; may be offset for timing_offset
    narration: str       # free text; sometimes contains order_id fragment


@dataclass
class GatewayRow:
    payment_id: str
    utr: str             # links to bank; may be blank for missing_ref
    amount: str          # net of fee, 2 dp
    fee: str             # 2 dp
    settled_at: str      # ISO datetime


@dataclass
class AnswerKeyRow:
    txn_id: str
    ground_truth_match: str   # pipe-separated utr(s)/payment_id, or "" for orphan
    noise_type: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _d(value: float, dp: int = 2) -> Decimal:
    """Round a float to a Decimal with `dp` decimal places."""
    quantizer = Decimal(10) ** -dp
    return Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP)


def _fmt(d: Decimal) -> str:
    return str(d)


def _noise_counts(n_rows: int) -> list[str]:
    """
    Return a shuffled list of `n_rows` noise-type labels that exactly
    sums to n_rows and approximates NOISE_WEIGHTS as closely as possible.
    Remainder is absorbed into 'clean'.
    """
    categories = list(NOISE_WEIGHTS.keys())
    counts: dict[str, int] = {}
    total_assigned = 0
    for cat in categories:
        if cat == "clean":
            continue
        cnt = math.floor(n_rows * NOISE_WEIGHTS[cat])
        counts[cat] = cnt
        total_assigned += cnt
    counts["clean"] = n_rows - total_assigned

    labels: list[str] = []
    for cat, cnt in counts.items():
        labels.extend([cat] * cnt)

    return labels  # caller should shuffle with seeded rng


def _write_csv(rows: list, path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(rows[0])])
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_dataset(
    n_rows: int = 300,
    seed: int = 42,
    output_dir: Path = Path("data"),
) -> Tuple[Path, Path, Path, Path]:
    """
    Generate the four CSVs and write them to *output_dir*.

    Returns a tuple of (ledger_path, bank_path, gateway_path, key_path).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)

    # Assign noise types to each row index
    labels = _noise_counts(n_rows)
    rng.shuffle(labels)

    ledger_rows: List[LedgerRow] = []
    bank_rows: List[BankRow] = []
    gateway_rows: List[GatewayRow] = []
    answer_key_rows: List[AnswerKeyRow] = []

    for i, noise_type in enumerate(labels):
        idx = i + 1  # 1-based for human-readable IDs

        txn_id = f"TXN_{idx:05d}"
        order_id = f"ORD_{idx:05d}"
        base_utr = f"UTR_{idx:05d}"
        base_pay = f"PAY_{idx:05d}"

        # Base amount: ₹100 – ₹10 000, rounded to 2 dp
        base_amount: Decimal = _d(rng.uniform(100.0, 10000.0))

        # Ledger timestamp: BASE_DATE + random offset in minutes
        minutes_offset = rng.randint(0, 525_600)  # up to ~1 year
        ledger_ts = BASE_DATE + timedelta(minutes=minutes_offset)
        ledger_ts_str = ledger_ts.strftime("%Y-%m-%dT%H:%M:%S")
        ledger_date_str = ledger_ts.strftime("%Y-%m-%d")

        # --- Build rows per noise type ---

        if noise_type == "clean":
            status = "captured"
            ledger_rows.append(LedgerRow(
                txn_id=txn_id, order_id=order_id,
                amount=_fmt(base_amount), currency="INR",
                timestamp=ledger_ts_str, status=status,
            ))
            fee = (base_amount * GATEWAY_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            gw_amount = base_amount - fee

            bank_rows.append(BankRow(
                utr=base_utr,
                amount=_fmt(base_amount),
                value_date=ledger_date_str,
                narration=f"NEFT/{order_id}/AXIS",
            ))
            gateway_rows.append(GatewayRow(
                payment_id=base_pay,
                utr=base_utr,
                amount=_fmt(gw_amount),
                fee=_fmt(fee),
                settled_at=ledger_ts_str,
            ))
            answer_key_rows.append(AnswerKeyRow(
                txn_id=txn_id,
                ground_truth_match=base_utr,
                noise_type=noise_type,
            ))

        elif noise_type == "rounding":
            status = "captured"
            drift = _d(rng.uniform(0.01, 2.00))
            sign = rng.choice([-1, 1])
            bank_amount = base_amount + sign * drift

            ledger_rows.append(LedgerRow(
                txn_id=txn_id, order_id=order_id,
                amount=_fmt(base_amount), currency="INR",
                timestamp=ledger_ts_str, status=status,
            ))
            fee = (bank_amount * GATEWAY_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            gw_amount = bank_amount - fee

            bank_rows.append(BankRow(
                utr=base_utr,
                amount=_fmt(bank_amount),
                value_date=ledger_date_str,
                narration=f"NEFT/{order_id}/AXIS",
            ))
            gateway_rows.append(GatewayRow(
                payment_id=base_pay,
                utr=base_utr,
                amount=_fmt(gw_amount),
                fee=_fmt(fee),
                settled_at=ledger_ts_str,
            ))
            answer_key_rows.append(AnswerKeyRow(
                txn_id=txn_id,
                ground_truth_match=base_utr,
                noise_type=noise_type,
            ))

        elif noise_type == "missing_ref":
            # UTR is blank on bank side; payment_id still exists on gateway
            status = "captured"
            ledger_rows.append(LedgerRow(
                txn_id=txn_id, order_id=order_id,
                amount=_fmt(base_amount), currency="INR",
                timestamp=ledger_ts_str, status=status,
            ))
            fee = (base_amount * GATEWAY_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            gw_amount = base_amount - fee

            bank_rows.append(BankRow(
                utr="",          # intentionally blank
                amount=_fmt(base_amount),
                value_date=ledger_date_str,
                narration=f"NEFT/{order_id}/AXIS",
            ))
            gateway_rows.append(GatewayRow(
                payment_id=base_pay,
                utr="",          # intentionally blank
                amount=_fmt(gw_amount),
                fee=_fmt(fee),
                settled_at=ledger_ts_str,
            ))
            # ground_truth_match uses payment_id since bank UTR is absent
            answer_key_rows.append(AnswerKeyRow(
                txn_id=txn_id,
                ground_truth_match=base_pay,
                noise_type=noise_type,
            ))

        elif noise_type == "duplicate":
            # One ledger row → two bank/gateway rows (duplicate settlement)
            status = "captured"
            utr_a = f"{base_utr}_A"
            utr_b = f"{base_utr}_B"
            pay_a = f"{base_pay}_A"
            pay_b = f"{base_pay}_B"

            ledger_rows.append(LedgerRow(
                txn_id=txn_id, order_id=order_id,
                amount=_fmt(base_amount), currency="INR",
                timestamp=ledger_ts_str, status=status,
            ))
            fee = (base_amount * GATEWAY_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            gw_amount = base_amount - fee

            for utr, pay in [(utr_a, pay_a), (utr_b, pay_b)]:
                bank_rows.append(BankRow(
                    utr=utr,
                    amount=_fmt(base_amount),
                    value_date=ledger_date_str,
                    narration=f"NEFT/{order_id}/AXIS",
                ))
                gateway_rows.append(GatewayRow(
                    payment_id=pay,
                    utr=utr,
                    amount=_fmt(gw_amount),
                    fee=_fmt(fee),
                    settled_at=ledger_ts_str,
                ))

            answer_key_rows.append(AnswerKeyRow(
                txn_id=txn_id,
                ground_truth_match=f"{utr_a}|{utr_b}",
                noise_type=noise_type,
            ))

        elif noise_type == "partial_refund":
            # One ledger row → two bank/gateway rows whose amounts sum to ledger amount
            # Use integer cents arithmetic for exact split
            status = "partial_refund"
            total_cents = int(base_amount * 100)
            split_pct = rng.uniform(0.30, 0.70)
            part_a_cents = int(total_cents * split_pct)
            part_b_cents = total_cents - part_a_cents
            part_a = Decimal(part_a_cents) / Decimal(100)
            part_b = Decimal(part_b_cents) / Decimal(100)

            utr_a = f"{base_utr}_A"
            utr_b = f"{base_utr}_B"
            pay_a = f"{base_pay}_A"
            pay_b = f"{base_pay}_B"

            ledger_rows.append(LedgerRow(
                txn_id=txn_id, order_id=order_id,
                amount=_fmt(base_amount), currency="INR",
                timestamp=ledger_ts_str, status=status,
            ))

            for part_amt, utr, pay in [
                (part_a, utr_a, pay_a),
                (part_b, utr_b, pay_b),
            ]:
                fee = (part_amt * GATEWAY_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                gw_amount = part_amt - fee
                bank_rows.append(BankRow(
                    utr=utr,
                    amount=_fmt(part_amt),
                    value_date=ledger_date_str,
                    narration=f"NEFT/{order_id}/AXIS",
                ))
                gateway_rows.append(GatewayRow(
                    payment_id=pay,
                    utr=utr,
                    amount=_fmt(gw_amount),
                    fee=_fmt(fee),
                    settled_at=ledger_ts_str,
                ))

            answer_key_rows.append(AnswerKeyRow(
                txn_id=txn_id,
                ground_truth_match=f"{utr_a}|{utr_b}",
                noise_type=noise_type,
            ))

        elif noise_type == "timing_offset":
            # Settlement date is 1–3 days after ledger timestamp
            status = "captured"
            offset_days = rng.randint(1, 3)
            settlement_date = (ledger_ts + timedelta(days=offset_days)).strftime("%Y-%m-%d")
            settled_at_str = (ledger_ts + timedelta(days=offset_days)).strftime("%Y-%m-%dT%H:%M:%S")

            ledger_rows.append(LedgerRow(
                txn_id=txn_id, order_id=order_id,
                amount=_fmt(base_amount), currency="INR",
                timestamp=ledger_ts_str, status=status,
            ))
            fee = (base_amount * GATEWAY_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            gw_amount = base_amount - fee

            bank_rows.append(BankRow(
                utr=base_utr,
                amount=_fmt(base_amount),
                value_date=settlement_date,
                narration=f"NEFT/{order_id}/AXIS",
            ))
            gateway_rows.append(GatewayRow(
                payment_id=base_pay,
                utr=base_utr,
                amount=_fmt(gw_amount),
                fee=_fmt(fee),
                settled_at=settled_at_str,
            ))
            answer_key_rows.append(AnswerKeyRow(
                txn_id=txn_id,
                ground_truth_match=base_utr,
                noise_type=noise_type,
            ))

        elif noise_type == "orphan":
            # No bank or gateway rows — true permanent exception
            status = "captured"
            ledger_rows.append(LedgerRow(
                txn_id=txn_id, order_id=order_id,
                amount=_fmt(base_amount), currency="INR",
                timestamp=ledger_ts_str, status=status,
            ))
            # ground_truth_match is empty string (null equivalent in CSV)
            answer_key_rows.append(AnswerKeyRow(
                txn_id=txn_id,
                ground_truth_match="",
                noise_type=noise_type,
            ))

    # Write CSVs
    ledger_path = output_dir / "internal_ledger.csv"
    bank_path = output_dir / "bank_settlement.csv"
    gateway_path = output_dir / "gateway_settlement.csv"
    key_path = output_dir / "answer_key.csv"

    _write_csv(ledger_rows, ledger_path)
    _write_csv(bank_rows, bank_path)
    _write_csv(gateway_rows, gateway_path)
    _write_csv(answer_key_rows, key_path)

    return ledger_path, bank_path, gateway_path, key_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic reconciliation data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--rows", type=int, default=300, help="Number of ledger rows.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    parser.add_argument("--out", type=Path, default=Path("data"), help="Output directory.")
    args = parser.parse_args()

    paths = generate_dataset(n_rows=args.rows, seed=args.seed, output_dir=args.out)
    print(f"[OK] Generated {args.rows} ledger rows (seed={args.seed})")
    for p in paths:
        print(f"  -> {p}")


if __name__ == "__main__":
    _cli()
