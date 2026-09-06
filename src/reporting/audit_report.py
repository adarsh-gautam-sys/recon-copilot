"""
Audit Report & Exception Reporting — Reconciliation Copilot (PRD §7.4, §9)

Inputs:
  - Combined list of MatchRecord objects from the deterministic matcher + LLM escalator.
  - answer_key.csv path (ground truth generated in Part 1).

Outputs:
  - AuditReport dataclass (returned).
  - outputs/report_<timestamp>.json (written to disk).
  - Human-readable console summary (printed).

Metrics computed (all against the answer key — PRD §9 "honest metrics"):
  - rule_match_rate     : rule-matched rows / total
  - llm_match_rate      : llm-matched (above threshold) rows / total
  - total_match_rate    : (rule + llm) matched / total
  - unresolved_rate     : unresolved rows / total
  - verified_match_rate : matches that agree with ground_truth_match / total
  - llm_precision       : LLM-matched rows agreeing with key / total LLM matches

Invariant enforced:
  Every input txn_id must appear exactly once in match_records.
  Missing or duplicate rows raise ValueError before any metric is computed.
"""

from __future__ import annotations

import csv
import json
import textwrap
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.matching.deterministic_matcher import MatchRecord


# ---------------------------------------------------------------------------
# Per-row audit entry
# ---------------------------------------------------------------------------

@dataclass
class AuditRow:
    txn_id: str
    noise_type: str                    # from answer_key
    ground_truth_match: str            # pipe-sep UTR(s) or "" for orphan
    match_type: str                    # rule | llm | unresolved
    engine: str                        # rule | llm | unresolved (same as match_type here)
    matched_bank_utr: Optional[str]
    matched_gateway_payment_id: Optional[str]
    confidence: float
    reasoning: Optional[str]
    exception_category: Optional[str]
    matches_ground_truth: bool         # True if matched UTR ∈ ground_truth, or correctly unresolved orphan


# ---------------------------------------------------------------------------
# Report summary
# ---------------------------------------------------------------------------

@dataclass
class AuditReport:
    run_id: str
    generated_at: str
    total_rows: int
    # Raw counts
    rule_matched: int
    llm_matched: int
    unresolved: int
    # Rates (0–1)
    rule_match_rate: float
    llm_match_rate: float
    total_match_rate: float
    unresolved_rate: float
    verified_match_rate: float         # fraction of rows with correct match vs answer key
    llm_precision: Optional[float]     # None when no LLM matches exist
    # Quality signal
    red_flag: bool                     # high match rate but low LLM precision
    # Breakdowns
    exception_breakdown: Dict[str, int]  # exception_category -> count
    noise_type_summary: Dict[str, dict]  # noise_type -> {total, matched, unresolved, correctly_matched}
    # Full per-row audit trail
    per_row_audit: List[dict]


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """
    Build an AuditReport from a combined list of MatchRecord objects.

    Parameters
    ----------
    outputs_dir : Path
        Directory where JSON reports are written.  Created if missing.
    llm_precision_red_flag_threshold : float
        If total_match_rate > this AND llm_precision < it, red_flag is set.
    """

    def __init__(
        self,
        outputs_dir: Path = Path("outputs"),
        llm_precision_red_flag_threshold: float = 0.70,
    ) -> None:
        self.outputs_dir = outputs_dir
        self._red_flag_threshold = llm_precision_red_flag_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        match_records: List[MatchRecord],
        answer_key_path: Path,
    ) -> AuditReport:
        """
        Validate completeness, compute metrics, write JSON, print summary.

        Raises
        ------
        ValueError
            If any txn_id appears more than once or is missing from match_records
            compared to the answer_key ledger set.
        """
        answer_key = self._load_answer_key(answer_key_path)
        self._validate_completeness(match_records, answer_key)

        run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        generated_at = datetime.now(tz=timezone.utc).isoformat()

        per_row: List[AuditRow] = self._build_per_row_audit(match_records, answer_key)
        metrics = self._compute_metrics(per_row)

        report = AuditReport(
            run_id=run_id,
            generated_at=generated_at,
            total_rows=len(per_row),
            **metrics,
            per_row_audit=[asdict(r) for r in per_row],
        )

        self._write_json(report)
        self._print_summary(report)
        return report

    # ------------------------------------------------------------------
    # Answer key loading
    # ------------------------------------------------------------------

    def _load_answer_key(self, path: Path) -> Dict[str, dict]:
        """Return {txn_id -> {ground_truth_match, noise_type}} from answer_key.csv."""
        key: Dict[str, dict] = {}
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                key[row["txn_id"]] = {
                    "ground_truth_match": row.get("ground_truth_match", ""),
                    "noise_type": row.get("noise_type", "unknown"),
                }
        return key

    # ------------------------------------------------------------------
    # Completeness enforcement
    # ------------------------------------------------------------------

    def _validate_completeness(
        self,
        records: List[MatchRecord],
        answer_key: Dict[str, dict],
    ) -> None:
        """Raise ValueError on duplicate or missing txn_ids."""
        record_ids = [r.ledger_txn_id for r in records]

        # Detect duplicates
        seen: set[str] = set()
        duplicates: list[str] = []
        for txn_id in record_ids:
            if txn_id in seen:
                duplicates.append(txn_id)
            seen.add(txn_id)
        if duplicates:
            raise ValueError(
                f"Duplicate txn_ids in match_records: {duplicates}. "
                "Every ledger row must appear exactly once."
            )

        # Detect missing rows
        record_set = set(record_ids)
        missing = sorted(set(answer_key.keys()) - record_set)
        if missing:
            raise ValueError(
                f"Missing txn_ids in match_records (present in answer_key but not in output): "
                f"{missing[:10]}{'…' if len(missing) > 10 else ''}. "
                f"Total missing: {len(missing)}."
            )

        # Detect extra rows (in records but not in answer_key)
        extra = sorted(record_set - set(answer_key.keys()))
        if extra:
            raise ValueError(
                f"Unexpected txn_ids in match_records (not in answer_key): "
                f"{extra[:10]}{'…' if len(extra) > 10 else ''}."
            )

    # ------------------------------------------------------------------
    # Per-row audit construction
    # ------------------------------------------------------------------

    def _build_per_row_audit(
        self,
        records: List[MatchRecord],
        answer_key: Dict[str, dict],
    ) -> List[AuditRow]:
        rows: List[AuditRow] = []
        for r in records:
            ak = answer_key[r.ledger_txn_id]
            gt_match = ak["ground_truth_match"]
            noise_type = ak["noise_type"]
            correct = self._check_ground_truth(r, gt_match)
            rows.append(
                AuditRow(
                    txn_id=r.ledger_txn_id,
                    noise_type=noise_type,
                    ground_truth_match=gt_match,
                    match_type=r.match_type,
                    engine=r.match_type,
                    matched_bank_utr=r.matched_bank_utr,
                    matched_gateway_payment_id=r.matched_gateway_payment_id,
                    confidence=r.confidence,
                    reasoning=r.reasoning,
                    exception_category=r.exception_category,
                    matches_ground_truth=correct,
                )
            )
        return rows

    @staticmethod
    def _check_ground_truth(record: MatchRecord, gt_match_str: str) -> bool:
        """
        Return True when the match record's outcome agrees with the answer key.

        Matching rows  : any matched identifier (UTR or payment_id) appears in the
                         pipe-separated ground_truth_match set.
        Unresolved rows: correct iff the answer_key has no ground_truth_match
                         (i.e., the row is a genuine orphan with no settlement).
        """
        gt_set: set[str] = set()
        if gt_match_str:
            gt_set = {v.strip() for v in gt_match_str.split("|") if v.strip()}

        if record.match_type in ("rule", "llm"):
            matched_ids: set[str] = set()
            if record.matched_bank_utr:
                matched_ids.update(
                    v.strip() for v in record.matched_bank_utr.split("|") if v.strip()
                )
            if record.matched_gateway_payment_id:
                matched_ids.update(
                    v.strip()
                    for v in record.matched_gateway_payment_id.split("|")
                    if v.strip()
                )
            return bool(matched_ids & gt_set)

        # unresolved: correct only when there truly is no ground-truth match
        return not gt_set

    # ------------------------------------------------------------------
    # Metric computation
    # ------------------------------------------------------------------

    def _compute_metrics(self, per_row: List[AuditRow]) -> dict:
        total = len(per_row)
        rule_matched = sum(1 for r in per_row if r.match_type == "rule")
        llm_matched = sum(1 for r in per_row if r.match_type == "llm")
        unresolved = sum(1 for r in per_row if r.match_type == "unresolved")

        verified_correct = sum(1 for r in per_row if r.matches_ground_truth)

        llm_rows = [r for r in per_row if r.match_type == "llm"]
        llm_correct = sum(1 for r in llm_rows if r.matches_ground_truth)
        llm_precision = llm_correct / len(llm_rows) if llm_rows else None

        def rate(n: int) -> float:
            return round(n / total, 6) if total else 0.0

        total_match_rate = rate(rule_matched + llm_matched)
        red_flag = (
            llm_precision is not None
            and total_match_rate > self._red_flag_threshold
            and llm_precision < self._red_flag_threshold
        )

        # Exception breakdown (unresolved rows only)
        exc_breakdown: Dict[str, int] = {}
        for r in per_row:
            if r.match_type == "unresolved" and r.exception_category:
                exc_breakdown[r.exception_category] = (
                    exc_breakdown.get(r.exception_category, 0) + 1
                )

        # Noise-type summary
        noise_summary: Dict[str, dict] = {}
        for r in per_row:
            nt = r.noise_type
            if nt not in noise_summary:
                noise_summary[nt] = {
                    "total": 0,
                    "matched": 0,
                    "unresolved": 0,
                    "correctly_matched": 0,
                }
            noise_summary[nt]["total"] += 1
            if r.match_type in ("rule", "llm"):
                noise_summary[nt]["matched"] += 1
                if r.matches_ground_truth:
                    noise_summary[nt]["correctly_matched"] += 1
            else:
                noise_summary[nt]["unresolved"] += 1

        return dict(
            rule_matched=rule_matched,
            llm_matched=llm_matched,
            unresolved=unresolved,
            rule_match_rate=rate(rule_matched),
            llm_match_rate=rate(llm_matched),
            total_match_rate=total_match_rate,
            unresolved_rate=rate(unresolved),
            verified_match_rate=rate(verified_correct),
            llm_precision=round(llm_precision, 6) if llm_precision is not None else None,
            red_flag=red_flag,
            exception_breakdown=exc_breakdown,
            noise_type_summary=noise_summary,
        )

    # ------------------------------------------------------------------
    # JSON output
    # ------------------------------------------------------------------

    def _write_json(self, report: AuditReport) -> Path:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.outputs_dir / f"report_{report.run_id}.json"
        payload = asdict(report)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return out_path

    # ------------------------------------------------------------------
    # Console summary
    # ------------------------------------------------------------------

    def _print_summary(self, r: AuditReport) -> None:
        W = 50
        sep = "=" * W

        def bar(n: int, total: int) -> str:
            pct = n / total * 100 if total else 0
            return f"{n:5d} / {total}  ({pct:5.1f}%)"

        lines = [
            sep,
            "  RECONCILIATION AUDIT REPORT",
            f"  Run ID    : {r.run_id}",
            f"  Generated : {r.generated_at}",
            f"  Total rows: {r.total_rows}",
            sep,
            "MATCH RATES",
            f"  Rule-matched      : {bar(r.rule_matched,   r.total_rows)}",
            f"  LLM-matched       : {bar(r.llm_matched,    r.total_rows)}",
            f"  Total matched     : {bar(r.rule_matched + r.llm_matched, r.total_rows)}",
            f"  Unresolved        : {bar(r.unresolved,     r.total_rows)}",
            f"  Verified (vs key) : {bar(round(r.verified_match_rate * r.total_rows), r.total_rows)}",
            "",
            "LLM PRECISION",
        ]

        if r.llm_matched == 0:
            lines.append("  No LLM matches in this run (all fell to unresolved).")
        else:
            llm_correct = round((r.llm_precision or 0.0) * r.llm_matched)
            lines.append(f"  LLM matches        : {r.llm_matched}")
            lines.append(f"  Correct vs key     : {llm_correct}")
            prec_str = f"{r.llm_precision:.1%}" if r.llm_precision is not None else "N/A"
            lines.append(f"  Precision          : {prec_str}")

        if r.red_flag:
            lines.append(
                "  [!] RED FLAG: high total match rate but low LLM precision — "
                "review LLM matches carefully."
            )

        lines += ["", "EXCEPTION BREAKDOWN (unresolved rows)"]
        if r.exception_breakdown:
            for cat, cnt in sorted(r.exception_breakdown.items()):
                lines.append(f"  {cat:<30s}: {cnt}")
        else:
            lines.append("  (none)")

        lines += ["", "NOISE TYPE SUMMARY"]
        for nt, stats in sorted(r.noise_type_summary.items()):
            lines.append(
                f"  {nt:<20s}: {stats['matched']:3d} matched,"
                f" {stats['unresolved']:3d} unresolved,"
                f" {stats['correctly_matched']:3d} correct"
                f"  (of {stats['total']} total)"
            )

        lines.append(sep)
        print("\n".join(lines))
