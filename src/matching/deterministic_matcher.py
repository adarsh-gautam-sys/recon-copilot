"""
Deterministic Matcher — Reconciliation Copilot (PRD §7.2)

Three-condition join:
  1. Amount within ±₹amount_tol (default ₹0.01)
  2. Shared reference: order_id fragment in bank narration
     (UTR/payment_id are bank↔gateway links, not ledger↔bank)
  3. Settlement date within date_window days (default 3) of ledger timestamp

Any row satisfying all three → match_type="rule", confidence=1.0.
Partial-refund case: two bank rows whose amounts sum to ledger amount are
treated as a single rule match.
Everything else → unmatched (passed to LLM stage).
"""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Internal parsed-row types (schema per PRD §6)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LedgerRecord:
    txn_id: str
    order_id: str
    amount: Decimal
    currency: str
    timestamp: datetime
    status: str


@dataclass(frozen=True)
class BankRecord:
    row_index: int          # 0-based, used for deduplication tracking
    utr: str                # may be blank
    amount: Decimal
    value_date: date
    narration: str


@dataclass(frozen=True)
class GatewayRecord:
    payment_id: str
    utr: str                # may be blank; links to BankRecord.utr
    amount: Decimal
    fee: Decimal
    settled_at: datetime


@dataclass
class MatchRecord:
    """PRD §6.5 — one record per ledger row, regardless of outcome."""
    match_id: str
    ledger_txn_id: str
    matched_bank_utr: Optional[str]               # pipe-separated for multi-row
    matched_gateway_payment_id: Optional[str]     # pipe-separated for multi-row
    match_type: str                               # "rule" | "llm" | "unresolved"
    confidence: float                             # 1.0 for rule
    reasoning: Optional[str]                      # null for rule
    exception_category: Optional[str]             # null unless unresolved


# ---------------------------------------------------------------------------
# CSV loaders
# ---------------------------------------------------------------------------

def _parse_decimal(s: str) -> Decimal:
    return Decimal(s.strip()) if s.strip() else Decimal("0")


def _parse_date(s: str) -> date:
    """Parse YYYY-MM-DD or ISO datetime; return the date portion."""
    s = s.strip()
    if "T" in s:
        return datetime.fromisoformat(s).date()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_datetime(s: str) -> datetime:
    return datetime.fromisoformat(s.strip())


def load_ledger(path: Path) -> List[LedgerRecord]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(LedgerRecord(
                txn_id=r["txn_id"],
                order_id=r["order_id"],
                amount=_parse_decimal(r["amount"]),
                currency=r["currency"],
                timestamp=_parse_datetime(r["timestamp"]),
                status=r["status"],
            ))
    return rows


def load_bank(path: Path) -> List[BankRecord]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for idx, r in enumerate(csv.DictReader(fh)):
            rows.append(BankRecord(
                row_index=idx,
                utr=r["utr"].strip(),
                amount=_parse_decimal(r["amount"]),
                value_date=_parse_date(r["value_date"]),
                narration=r["narration"].strip(),
            ))
    return rows


def load_gateway(path: Path) -> List[GatewayRecord]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(GatewayRecord(
                payment_id=r["payment_id"].strip(),
                utr=r["utr"].strip(),
                amount=_parse_decimal(r["amount"]),
                fee=_parse_decimal(r["fee"]),
                settled_at=_parse_datetime(r["settled_at"]),
            ))
    return rows


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

class DeterministicMatcher:
    """
    Rule-based reconciliation engine.

    Parameters
    ----------
    amount_tol : Decimal | float
        Maximum absolute difference between ledger and bank amounts to
        consider an amount match.  Default ₹0.01.
    date_window : int
        Maximum number of calendar days between ledger timestamp and bank
        value_date to consider a date match.  Default 3.
    """

    def __init__(
        self,
        amount_tol: Decimal | float = Decimal("0.01"),
        date_window: int = 3,
    ) -> None:
        self.amount_tol = Decimal(str(amount_tol))
        self.date_window = date_window

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(
        self,
        ledger: List[LedgerRecord],
        bank: List[BankRecord],
        gateway: List[GatewayRecord],
    ) -> Tuple[List[MatchRecord], List[LedgerRecord]]:
        """
        Run deterministic matching.

        Returns
        -------
        matched : list[MatchRecord]   — rule matches, confidence 1.0
        unmatched : list[LedgerRecord] — rows for LLM escalation
        """
        # Pre-build gateway lookups
        gw_by_utr: dict[str, GatewayRecord] = {
            gw.utr: gw for gw in gateway if gw.utr
        }
        gw_blank: List[GatewayRecord] = [gw for gw in gateway if not gw.utr]

        used_bank_indices: set[int] = set()
        used_gw_payment_ids: set[str] = set()

        matched: List[MatchRecord] = []
        unmatched: List[LedgerRecord] = []

        for ledger_row in ledger:
            ledger_date = ledger_row.timestamp.date()

            # ----------------------------------------------------------------
            # Phase 1 — Single-row amount match
            # Find bank rows where all 3 conditions hold simultaneously
            # ----------------------------------------------------------------
            single_candidates: List[BankRecord] = [
                b for b in bank
                if b.row_index not in used_bank_indices
                and self._amount_ok(b.amount, ledger_row.amount)
                and self._ref_ok(b, ledger_row)
                and self._date_ok(b.value_date, ledger_date)
            ]

            if single_candidates:
                # One or more bank rows satisfy all 3 conditions.
                # Record all (handles both standard match and duplicate).
                bank_utrs = [b.utr or "" for b in single_candidates]
                gw_records = [
                    self._find_gateway(b, gw_by_utr, gw_blank, ledger_row.amount, ledger_date)
                    for b in single_candidates
                ]
                gw_pay_ids = [gw.payment_id if gw else None for gw in gw_records]

                matched_bank_utr = "|".join(u for u in bank_utrs if u) or None
                matched_gw_pay = "|".join(p for p in gw_pay_ids if p) or None

                # Mark rows as consumed
                for b in single_candidates:
                    used_bank_indices.add(b.row_index)
                for gw in gw_records:
                    if gw:
                        used_gw_payment_ids.add(gw.payment_id)

                matched.append(MatchRecord(
                    match_id=f"MATCH_{ledger_row.txn_id}",
                    ledger_txn_id=ledger_row.txn_id,
                    matched_bank_utr=matched_bank_utr,
                    matched_gateway_payment_id=matched_gw_pay,
                    match_type="rule",
                    confidence=1.0,
                    reasoning=None,
                    exception_category=None,
                ))
                continue

            # ----------------------------------------------------------------
            # Phase 2 — Partial-refund pair detection
            # Find ALL bank rows matching by reference + date (ignoring amount),
            # then check if any pair's amounts sum to the ledger amount.
            # ----------------------------------------------------------------
            narration_date_candidates: List[BankRecord] = [
                b for b in bank
                if b.row_index not in used_bank_indices
                and self._ref_ok(b, ledger_row)
                and self._date_ok(b.value_date, ledger_date)
            ]

            partial_match: Optional[Tuple[BankRecord, BankRecord]] = None
            for b_a, b_b in itertools.combinations(narration_date_candidates, 2):
                pair_sum = b_a.amount + b_b.amount
                if abs(pair_sum - ledger_row.amount) <= self.amount_tol:
                    partial_match = (b_a, b_b)
                    break

            if partial_match:
                b_a, b_b = partial_match
                gw_a = self._find_gateway(b_a, gw_by_utr, gw_blank, b_a.amount, ledger_date)
                gw_b = self._find_gateway(b_b, gw_by_utr, gw_blank, b_b.amount, ledger_date)

                utr_parts = [u for u in (b_a.utr, b_b.utr) if u]
                pay_parts = [
                    gw.payment_id for gw in (gw_a, gw_b) if gw and gw.payment_id
                ]

                used_bank_indices.add(b_a.row_index)
                used_bank_indices.add(b_b.row_index)
                if gw_a:
                    used_gw_payment_ids.add(gw_a.payment_id)
                if gw_b:
                    used_gw_payment_ids.add(gw_b.payment_id)

                matched.append(MatchRecord(
                    match_id=f"MATCH_{ledger_row.txn_id}_SPLIT",
                    ledger_txn_id=ledger_row.txn_id,
                    matched_bank_utr="|".join(utr_parts) or None,
                    matched_gateway_payment_id="|".join(pay_parts) or None,
                    match_type="rule",
                    confidence=1.0,
                    reasoning=None,
                    exception_category=None,
                ))
                continue

            # ----------------------------------------------------------------
            # No match found — pass to LLM stage
            # ----------------------------------------------------------------
            unmatched.append(ledger_row)

        return matched, unmatched

    def load_and_match(
        self,
        data_dir: Path = Path("data"),
    ) -> Tuple[List[MatchRecord], List[LedgerRecord]]:
        """Convenience: load CSVs from *data_dir* then run match()."""
        data_dir = Path(data_dir)
        ledger = load_ledger(data_dir / "internal_ledger.csv")
        bank = load_bank(data_dir / "bank_settlement.csv")
        gateway = load_gateway(data_dir / "gateway_settlement.csv")
        return self.match(ledger, bank, gateway)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _amount_ok(self, bank_amount: Decimal, ledger_amount: Decimal) -> bool:
        return abs(bank_amount - ledger_amount) <= self.amount_tol

    def _ref_ok(self, bank: BankRecord, ledger: LedgerRecord) -> bool:
        """
        Reference match: ledger order_id must appear somewhere in bank narration.
        This covers both UTR-present and missing_ref rows because the generator
        always embeds order_id in narration.
        """
        return ledger.order_id in bank.narration

    def _date_ok(self, settlement_date: date, ledger_date: date) -> bool:
        return abs((settlement_date - ledger_date).days) <= self.date_window

    def _find_gateway(
        self,
        bank: BankRecord,
        gw_by_utr: dict[str, GatewayRecord],
        gw_blank: List[GatewayRecord],
        match_amount: Decimal,
        ledger_date: date,
    ) -> Optional[GatewayRecord]:
        """
        Find the gateway row that corresponds to this bank row.

        If bank.utr is non-blank → direct UTR lookup.
        If bank.utr is blank → fallback: find a gateway row where:
          - gateway.utr is also blank
          - gateway.amount + gateway.fee ≈ match_amount (within tol)
          - gateway.settled_at date within date_window of ledger_date
        """
        if bank.utr:
            return gw_by_utr.get(bank.utr)

        # Fallback for missing_ref rows
        for gw in gw_blank:
            gross = gw.amount + gw.fee
            if (
                abs(gross - match_amount) <= self.amount_tol
                and self._date_ok(gw.settled_at.date(), ledger_date)
            ):
                return gw
        return None
