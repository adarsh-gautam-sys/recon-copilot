# PRD — Reconciliation Copilot

**Course:** Foundations of Agentic AI — College Course Submission
**One-liner:** A deterministic-first reconciliation agent that matches transactions across bank settlement, payment-gateway settlement, and internal ledger sources — escalating only genuinely ambiguous rows to an LLM, and reporting an honest match rate plus a categorized exception list with a full audit trail.

---

## 1. Problem Statement

Finance teams reconcile money across 3+ disconnected sources (bank, gateway, internal ledger) by hand or with brittle rule scripts. Mismatches come from rounding drift, missing references, partial refunds, timing offsets, and duplicate entries. Nobody wants an agent that "looks smart" — they want one that resolves what it can prove, and clearly flags what it can't.

Core thesis for **Foundations of Agentic AI**: *"verification capacity, not generation speed, is the bottleneck."* This PRD is built around that principle — the agent is a hybrid neuro-symbolic verifier, not an unconstrained generator.

## 2. Goals

- Reconcile 3 synthetic financial data sources with deterministic rules first.
- Escalate only unresolved rows to an LLM, which proposes (never silently commits) a match with reasoning + confidence.
- Hard-gate: any LLM-proposed match below a confidence threshold becomes an **exception**, not a forced match.
- Report: match rate, exception list categorized by failure reason, full per-row audit trail (which engine resolved it, why).
- Ship as a reproducible repository with an interactive web verification console, FastAPI wrapper, and comprehensive documentation for academic evaluation.

## 3. Non-Goals

- No production-grade auth, multi-tenant, or persistence layer — this is a batch-run demo, not a service to harden.
- No live bank/gateway integration — all data is synthetic, generated with a known answer key.
- No attempt to auto-resolve every row. An honest "unresolved, here's why" beats a forced 100% match rate.
- GPU-accelerated (cuDF/RAPIDS) execution is a stretch goal, not a requirement — see §8 Technical Decisions.

## 4. Users & Context

Simulated user: a finance-ops analyst who currently reconciles settlement reports by hand in spreadsheets. The system is a batch tool they'd run at end-of-day/week, not a real-time service.

## 5. System Architecture

```
                ┌─────────────────────┐
                │  Synthetic Data Gen  │  (bank / gateway / ledger CSVs + answer key)
                └──────────┬───────────┘
                           │
                ┌──────────▼───────────┐
                │ Deterministic Matcher │  (rule-based: amount + ref + date window)
                └──────────┬───────────┘
                    matched │  unmatched
                           │
                ┌──────────▼───────────┐
                │   LLM Escalator       │  (proposes match + reasoning + confidence)
                └──────────┬───────────┘
                 confidence │ below threshold
                    ≥ thresh│  → exception
                           │
                ┌──────────▼───────────┐
                │  Audit + Exception    │  (per-row trail, match-rate metric,
                │  Report Generator     │   categorized exceptions)
                └──────────┬───────────┘
                           │
                ┌──────────▼───────────┐
                │   FastAPI wrapper     │  (POST /reconcile → JSON report)
                └───────────────────────┘
```

## 6. Data Model

### 6.1 Internal Ledger (`internal_ledger.csv`)
| field | type | notes |
|---|---|---|
| `txn_id` | string | primary key |
| `order_id` | string | |
| `amount` | decimal | in INR |
| `currency` | string | always "INR" for v1 |
| `timestamp` | datetime | transaction time |
| `status` | enum | `captured`, `refunded`, `partial_refund` |

### 6.2 Bank Settlement (`bank_settlement.csv`)
| field | type | notes |
|---|---|---|
| `utr` | string | may be **missing** (noise) |
| `amount` | decimal | may have **rounding drift** of ±₹0.01–2 |
| `value_date` | date | may be offset +1–3 days from ledger timestamp |
| `narration` | string | free text, sometimes contains order_id fragment |

### 6.3 Gateway Settlement (`gateway_settlement.csv`)
| field | type | notes |
|---|---|---|
| `payment_id` | string | |
| `utr` | string | links to bank settlement, may be missing |
| `amount` | decimal | net of fee |
| `fee` | decimal | |
| `settled_at` | datetime | |

### 6.4 Answer Key (`answer_key.csv`) — generator-only, used for evaluation
| field | type | notes |
|---|---|---|
| `txn_id` | string | |
| `ground_truth_match` | string or null | the *true* matching bank/gateway row(s), or null if intentionally unresolvable |
| `noise_type` | enum | `clean`, `rounding`, `missing_ref`, `duplicate`, `partial_refund`, `timing_offset`, `orphan` |

### 6.5 Match Record (output)
| field | type | notes |
|---|---|---|
| `match_id` | string | |
| `ledger_txn_id` | string | |
| `matched_bank_utr` | string or null | |
| `matched_gateway_payment_id` | string or null | |
| `match_type` | enum | `rule`, `llm`, `unresolved` |
| `confidence` | float 0–1 | 1.0 for rule matches |
| `reasoning` | string | required for `llm` type, null for `rule` |
| `exception_category` | string or null | populated only when `match_type == unresolved` |

## 7. Functional Requirements

### 7.1 Synthetic Data Generator
- Generate 200–500 ledger rows.
- Inject noise per the categories in §6.4, at roughly: 70% clean, 10% rounding, 8% missing_ref, 5% duplicate, 4% partial_refund, 2% timing_offset, 1% orphan (true exception, no match exists anywhere).
- Must write a matching `answer_key.csv` so match rate can be scored objectively later.
- Deterministic seed so runs are reproducible.

### 7.2 Deterministic Match Engine
- Join ledger ↔ gateway ↔ bank on: exact `amount` match (within ±₹0.01) + shared reference (`utr`/`payment_id`/order_id fragment in narration) + `timestamp` within a configurable date window (default 3 days).
- Anything satisfying all three conditions is a `rule` match at confidence 1.0 — no LLM involved.
- Everything else passes to §7.3.

### 7.3 LLM Escalation Layer
- Input: one unmatched ledger row + top-N candidate bank/gateway rows (by amount proximity).
- Output (structured/JSON): proposed match (or "no match"), reasoning (1–2 sentences), confidence (0–1).
- **Hard gate:** confidence < 0.7 (configurable) → forced to `unresolved`, never auto-committed regardless of what the LLM says.
- Must handle "no plausible match" as a valid LLM output, not force a match.

### 7.4 Audit Trail & Exception Reporting
- Every row gets exactly one match record (§6.5), regardless of outcome.
- Report includes: overall match rate (rule-only, rule+LLM, and total), a breakdown by `exception_category`, and the full per-row audit trail.
- Match rate must be computed **against the answer key**, not self-reported — enforcing objective ground-truth evaluation of agentic precision.

### 7.5 API & Interactive Workbench Layer
- `GET /` — serves the interactive Statistical Bulletin & Verification Workbench UI.
- `POST /reconcile` — runs the full pipeline on the three CSVs, returns the report as JSON.
- `GET /report/{run_id}` — fetch a prior run's report.

## 8. Technical Decisions & Trade-offs (Agentic Systems Engineering)

- **cuDF/RAPIDS vs pandas:** RAPIDS needs an NVIDIA GPU + CUDA toolchain — a real risk to lose build time on setup. Decision: build on pandas first (correctness), and only swap in `cudf.pandas` (drop-in accelerator mode, same API) if GPU capacity is needed. Document this trade-off explicitly in the README as an engineering design decision.
- **LLM provider:** any structured-output-capable API (Claude/OpenAI/OpenRouter) plus a deterministic local heuristic agent simulator (`heuristic` mode) so evaluators can test bounded escalation with zero API keys.
- **Confidence threshold:** start at 0.70, and expose the threshold distribution so the hard gate is justified empirically.

## 9. Metrics & Evaluation

- **Rule match rate** = rule-matched rows / total rows.
- **LLM-assisted match rate** = LLM-matched (above threshold) rows / total rows.
- **Precision of LLM matches** = LLM matches that agree with `ground_truth_match` / total LLM matches (computed against answer key — the primary credibility metric).
- **Unresolved rate**, broken down by `exception_category`.
- Report all four numbers together. A high total match rate with low LLM precision is a red flag the report should surface, not hide.

## 10. Deliverables

- Complete repository structured per §12 (Repo Layout).
- Interactive web verification workbench served by FastAPI (`GET /`).
- README: problem formulation, hybrid agent architecture diagram, engineering trade-offs in §8, and failure-mode analysis.
- This PRD, committed to the repo (`/docs/PRD.md`).

## 11. Testing Requirements

- Unit tests for the data generator (correct noise distribution, reproducible seed, answer key consistency).
- Unit tests for the deterministic matcher (exact match, rounding-within-tolerance match, date-window edge cases, no-false-positive on genuinely different transactions).
- Unit tests for the LLM escalator using a **mocked** LLM client (never call a real API in tests) — confidence gating logic, "no match" handling, malformed-response handling.
- Unit tests for the report generator (match-rate math, exception categorization, audit trail completeness — every input row appears exactly once in output).
- One integration test: run the full pipeline end-to-end on a small fixed fixture and assert the final match rate against a known expected value.

## 12. Repo Layout

```
reconciliation-copilot/
├── README.md
├── PRODUCT.md
├── DESIGN.md
├── requirements.txt
├── docs/
│   └── PRD.md
├── src/
│   ├── config.py
│   ├── data_gen/
│   │   └── generate_synthetic_data.py
│   ├── matching/
│   │   ├── deterministic_matcher.py
│   │   └── llm_escalator.py
│   ├── reporting/
│   │   └── audit_report.py
│   └── api/
│       ├── main.py
│       ├── pipeline.py
│       └── static/
│           └── index.html
├── data/
│   ├── bank_settlement.csv
│   ├── gateway_settlement.csv
│   ├── internal_ledger.csv
│   └── answer_key.csv
├── tests/
│   ├── test_data_gen.py
│   ├── test_deterministic_matcher.py
│   ├── test_llm_escalator.py
│   ├── test_audit_report.py
│   ├── test_integration.py
│   └── test_api.py
└── outputs/
    └── (generated reports land here)
```

## 13. Risks

| Risk | Mitigation |
|---|---|
| Unconstrained LLM hallucination on financial data | Hard confidence gate (`>= 0.70`) enforced in Python code; deterministic rules execute first |
| Over-claiming accuracy | Always report against `answer_key.csv`, never a self-reported number |
| GPU setup overhead | pandas-first architecture with `cudf.pandas` drop-in compatibility |
| Evaluators lacking external LLM API keys | Ship a one-command entry point, offline rule baseline, and bounded heuristic agent simulator alongside live OpenRouter mode |
