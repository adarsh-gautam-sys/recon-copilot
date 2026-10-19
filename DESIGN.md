---
name: Reconciliation Copilot — Central Bank Statistical Bulletin
description: Neuro-symbolic financial settlement verification workbench inspired by Bundesbank and RBI statistical yearbooks.
colors:
  sheet-bg: "#FBFBF9"
  sheet-tint: "#F3F1EC"
  sheet-elevated: "#ECE9E1"
  ink-primary: "#111315"
  ink-secondary: "#3E4348"
  ink-muted: "#5C6268"
  rule-heavy: "#111315"
  rule-hairline: "#C9C5BA"
  verify-green: "#046A38"
  verify-green-bg: "#E6F2EC"
  escalate-navy: "#1B365D"
  escalate-navy-bg: "#E8EEF5"
  gate-vermilion: "#D9381E"
  gate-vermilion-bg: "#FBEAE7"
typography:
  display:
    fontFamily: "'Libre Baskerville', Georgia, serif"
    fontSize: "clamp(1.75rem, 2.6vw, 2.45rem)"
    fontWeight: 700
    lineHeight: 1.18
    letterSpacing: "-0.02em"
  body:
    fontFamily: "'Public Sans', -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  mono:
    fontFamily: "'JetBrains Mono', monospace"
    fontSize: "0.81rem"
    fontWeight: 500
    lineHeight: 1.45
    letterSpacing: "0.01em"
rounded:
  none: "0px"
spacing:
  xs: "6px"
  sm: "12px"
  md: "20px"
  lg: "32px"
components:
  button-primary:
    backgroundColor: "{colors.ink-primary}"
    textColor: "{colors.sheet-bg}"
    rounded: "{rounded.none}"
    padding: "9px 18px"
  button-primary-hover:
    backgroundColor: "{colors.verify-green}"
  button-ghost:
    backgroundColor: "{colors.sheet-tint}"
    textColor: "{colors.ink-primary}"
    rounded: "{rounded.none}"
    padding: "6px 10px"
---

## Overview

Reconciliation Copilot adopts the visual authority of a **Central Bank Statistical Bulletin & Academic Monograph** (Bundesbank / Reserve Bank of India statistical yearbooks) for the **Foundations of Agentic AI** course submission. Every claim is proved inline by exposed three-leg settlement tables (`[Internal Ledger] <-> [Gateway] <-> [Bank UTR]`), double-underlined accounting invariants, and a live interactive confidence gate ($\tau \ge 0.70$).

## Colors

- **Grounds:** Warm uncoated alabaster sheet (`#FBFBF9`), tinted control panels (`#F3F1EC`), and elevated cells (`#ECE9E1`).
- **Ink & Rules:** Carbon black (`#111315`) for primary text and structural `2px` rules; warm stone (`#C9C5BA`) for `1px` cell hairlines.
- **Verification Semantics:**
  - `verify-green` (`#046A38` on `#E6F2EC`): Deterministic Stage 1 rule matches and verified ground-truth alignment.
  - `escalate-navy` (`#1B365D` on `#E8EEF5`): Stage 2 bounded LLM proposals passing the $\tau \ge 0.70$ confidence gate.
  - `gate-vermilion` (`#D9381E` on `#FBEAE7`): Stage 3 hard-gated exceptions ($<\tau$), unresolved orphans, and ground-truth discrepancies.

## Typography

- **Headings (`Libre Baskerville`):** Editorial statistical authority for the masthead and major section headings, never preceded by decorative kickers or eyebrows.
- **Prose (`Public Sans`):** High-legibility institutional sans-serif constrained to a `65–70ch` measure.
- **Tabular Figures (`JetBrains Mono`):** Used for all transaction IDs, UTRs, INR currency figures, confidence scores, and mathematical formulas with `font-variant-numeric: tabular-nums`.

## Layout

- Framed inside a centered `1440px` statistical sheet with `4px` top and bottom carbon rules.
- Descends from the two-column **Masthead & Batch Console** into the **4-Column Statistical Balance Strip**, the **Interactive Confidence Gate ($\tau$) & Ground-Truth Verification Band**, the **7-Archetype Noise Terrain Roster**, and the **Three-Leg Settlement Audit Table**.

## Elevation & Depth

- Hierarchy is established through exposed `1px` and `2px` solid rules and `3px double` accounting underlines rather than floating cards or blur effects.
- The outer bulletin sheet uses a single subtle paper shadow (`0 12px 28px -10px rgba(17, 19, 21, 0.07)`).

## Shapes

- Strict `0px` border radius across all tables, inputs, buttons, and stamp badges to preserve the precision of printed statistical tables.

## Components

- **Statistical Balance Cells:** Four equal columns with double-underlined figures and bottom accounting invariant footnotes (`Rule + LLM + Unresolved = Total`).
- **Confidence Gate ($\tau$) Scrubber:** Range input bound live to the client-side and server-side threshold gate so evaluators can test permissive vs. strict gating in real time.
- **7-Archetype Noise Roster:** Interactive proportional bar and 7-cell grid (`clean`, `rounding`, `missing_ref`, `duplicate`, `partial_refund`, `timing_offset`, `orphan`) that filters the ledger on click.
- **Expandable Three-Leg Audit Rows:** Clicking any transaction row opens a forensic trace drawer with the full three-leg settlement IDs, ground-truth key status, and stage reasoning.

## Do's and Don'ts

- **Do** enforce `font-variant-numeric: tabular-nums` on every numeric, currency, percentage, and confidence column.
- **Do** pair every aggregate match rate with its ground-truth `answer_key.csv` verification rate and LLM precision.
- **Don't** add eyebrows/kickers above headings, rounded SaaS KPI cards, decorative sparklines, or unverified AI chat chrome.
