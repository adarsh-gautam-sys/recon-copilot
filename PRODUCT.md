# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: Static HTML, CSS, and vanilla JavaScript served directly by the existing FastAPI backend (`src/api/main.py`) with zero Node build step so the entire demo runs in a single command.

## Users

1. **Academic Evaluators & Faculty (Foundations of Agentic AI):** Course instructors and peers evaluating the college submission on hybrid neuro-symbolic agent design, bounded LLM escalation, code-enforced confidence gating (`>= 0.70`), and objective evaluation against a ground-truth answer key.
2. **Finance-Ops Controllers & Analysts:** Reconcile end-of-day or end-of-week settlement reports across three disconnected sources (internal ledger, payment gateway settlement, and bank settlement). They need to separate proven matches from ambiguous discrepancies without trusting silent AI guesses.

## Product Purpose

Reconciliation Copilot is a deterministic-first financial reconciliation verifier. It resolves every transaction it can prove mathematically via deterministic rules, escalates only genuinely ambiguous rows to an LLM for proposed matching with reasoning and confidence scores, and hard-gates any proposal below a `0.70` confidence threshold into a categorized exception queue with a full per-row audit trail.

Success means zero silent false matches (`red_flag == false`), 100% row accounting completeness (`rule_matched + llm_matched + unresolved == total_rows`), and an exception queue that tells a finance controller exactly why a row failed (`orphan`, `duplicate`, `missing_ref`, `partial_refund`, `low_confidence`).

## Positioning

**"Verification capacity, not generation speed, is the bottleneck."** Unlike generic AI wrappers that force 100% match rates by guessing on noisy financial records, Reconciliation Copilot treats the LLM strictly as a bounded escalator behind a deterministic rule engine and enforces the confidence threshold in Python code rather than prompt instructions.

## Operating Context

- Runs as a batch verification workbench over three CSV sources (`internal_ledger.csv`, `gateway_settlement.csv`, `bank_settlement.csv`) plus a generator-only `answer_key.csv` (300 default rows across 7 noise categories: `clean`, `rounding`, `missing_ref`, `duplicate`, `partial_refund`, `timing_offset`, `orphan`).
- Supports two execution modes via `POST /reconcile`:
  - `offline` mode: deterministic heuristic simulator requiring no external API key.
  - `openrouter` mode: live LLM escalation (`z-ai/glm-5.2:free` default) with configurable rate-limit delay.
- Persists timestamped JSON audit reports in `outputs/report_{run_id}.json`, retrievable via `GET /report/{run_id}`.

## Capabilities and Constraints

- **Deterministic Engine (`src/matching/deterministic_matcher.py`):** Matches across ledger, gateway, and bank within ±₹0.01 rounding tolerance, shared UTR/order reference, and a ±3-day settlement window.
- **LLM Escalator (`src/matching/llm_escalator.py`):** Evaluates unmatched rows, returning proposed match IDs, confidence (`0.0–1.0`), reasoning, and exception category. Hard code gate rejects `< 0.70` confidence.
- **Audit Report (`src/reporting/audit_report.py`):** Computes `rule_match_rate`, `llm_match_rate`, `verified_match_rate`, `llm_precision`, `red_flag` (true if any LLM match disagrees with `answer_key.csv`), `exception_breakdown`, `noise_type_summary`, and `per_row_audit`.
- **Non-Goals:** No multi-tenant auth, no live bank API credentials, no database persistence beyond JSON report files, and no forced auto-resolution of unresolvable rows.

## Evidence on Hand

- Synthetic dataset generator and pre-generated 300-row CSVs in `data/` (`internal_ledger.csv`, `bank_settlement.csv`, `gateway_settlement.csv`, `answer_key.csv`).
- Historical audit report JSONs in `outputs/report_*.json`.
- FastAPI backend in `src/api/main.py` (`/health`, `POST /reconcile`, `GET /report/{run_id}`).
- 35 passing pytest tests across `tests/`.
- No real customer logos, external testimonials, or live production bank data exist; all financial transaction rows are synthetic and must be labeled as synthetic benchmark data.

## Product Principles

1. **Prove first, escalate second:** Never spend LLM tokens or introduce probabilistic risk on a row that deterministic rules (amount, reference, date window) can verify.
2. **Code gates over prompt promises:** Confidence thresholds (`>= 0.70`) and completeness invariants (`rule + llm + unresolved == total`) are enforced in Python code, never left to LLM compliance.
3. **Honest exceptions beat forced matches:** An unresolved row with a clear failure category (`orphan`, `duplicate`, `partial_refund`) is a successful verification outcome; a wrong match is a critical failure (`red_flag`).
4. **Every number traces to a row:** Aggregate metrics must always drill down to the exact `[Ledger] <-> [Gateway] <-> [Bank]` triangle, engine source, delta, and reasoning.
