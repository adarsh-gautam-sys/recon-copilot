"""
Reconciliation Copilot — Streamlit Deployment App
Foundations of Agentic AI Capstone Project

Run locally:
    streamlit run streamlit_app.py

Supports Ollama Cloud Free Tier (qwen3.1 / gemma4), Bounded Agent Simulator
(Heuristic), and Deterministic Rule Baseline (Offline).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from src.api.pipeline import PipelineRequest, run_pipeline

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUTS_DIR = BASE_DIR / "outputs"

st.set_page_config(
    page_title="Reconciliation Copilot — Foundations of Agentic AI",
    page_icon="⚖️",
    layout="wide",
)

# Central Bank Statistical Bulletin styling
st.markdown(
    """
    <style>
    .stApp { background-color: #FBFBF9; color: #111315; }
    .bulletin-header {
        border-top: 2px solid #111315;
        border-bottom: 2px solid #111315;
        padding: 1rem 0;
        margin-bottom: 1.25rem;
    }
    .bulletin-meta {
        font-family: monospace;
        font-size: 0.78rem;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #4A4F55;
    }
    .stat-box {
        border: 1px solid #111315;
        background: #F3F2EE;
        padding: 0.9rem 1rem;
    }
    .stat-label {
        font-family: monospace;
        font-size: 0.75rem;
        text-transform: uppercase;
        color: #4A4F55;
    }
    .stat-value {
        font-family: monospace;
        font-size: 1.85rem;
        font-weight: 700;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar Controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚙️ Agent Execution Controls")
    mode_label = st.selectbox(
        "Agent Escalation Engine",
        options=[
            "Bounded Agent Simulator (Heuristic)",
            "Ollama Cloud Free Tier (qwen3.1 / gemma4)",
            "Deterministic Rule Baseline (Offline)",
        ],
        index=0,
    )
    mode_map = {
        "Bounded Agent Simulator (Heuristic)": "heuristic",
        "Ollama Cloud Free Tier (qwen3.1 / gemma4)": "ollama",
        "Deterministic Rule Baseline (Offline)": "offline",
    }
    llm_mode = mode_map[mode_label]

    ollama_model = st.selectbox(
        "Ollama Model (Cloud Free Tier)",
        options=["qwen3.1", "gemma4"],
        index=0,
        disabled=(llm_mode != "ollama"),
    )
    default_host = os.environ.get("OLLAMA_HOST", "https://ollama.com/v1")
    ollama_host = st.text_input(
        "Ollama Endpoint Host",
        value=default_host,
        disabled=(llm_mode != "ollama"),
        help="Ollama Cloud Free Tier (https://ollama.com/v1) or local daemon (http://localhost:11434/v1).",
    )
    secret_key = ""
    try:
        secret_key = st.secrets.get("OLLAMA_API_KEY", "")
    except Exception:
        secret_key = ""
    ollama_api_key = st.text_input(
        "Ollama Cloud API Key",
        value=os.environ.get("OLLAMA_API_KEY", secret_key),
        type="password",
        disabled=(llm_mode != "ollama"),
        help="Set OLLAMA_API_KEY in Streamlit Secrets or paste here for Ollama Cloud Free Tier.",
    )

    st.divider()
    confidence_threshold = st.slider(
        "Code-Enforced Confidence Gate (τ)",
        min_value=0.10,
        max_value=0.99,
        value=0.70,
        step=0.01,
        help="Hard code-level gate: any LLM proposal with confidence < τ is forced to unresolved.",
    )
    n_rows = st.number_input("Batch Rows", min_value=20, max_value=5000, value=300, step=10)
    seed = st.number_input("Random Seed", min_value=1, max_value=99999, value=42, step=1)
    run_clicked = st.button("▶ EXECUTE VERIFICATION BATCH", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Masthead
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="bulletin-header">
        <div class="bulletin-meta">
            FOUNDATIONS OF AGENTIC AI // CAPSTONE SUBMISSION &middot; SERIES IV NEURO-SYMBOLIC FINANCIAL VERIFICATION
        </div>
        <h1 style="margin: 0.35rem 0; font-family: Georgia, serif;">
            Reconciliation Copilot: Deterministic-First Settlement Verifier
        </h1>
        <p style="margin: 0; color: #333;">
            <strong>“Verification capacity, not generation speed, is the bottleneck.”</strong>
            Reconciles three disconnected financial sources (Internal Ledger, Payment Gateway, and Bank UTR)
            by executing deterministic rules first, escalating only ambiguous discrepancies to an Ollama LLM
            (<code>qwen3.1</code> or <code>gemma4</code> Cloud Free Tier), and enforcing a hard code-level confidence gate
            (&tau; &ge; 0.70).
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Pipeline Execution (cached in session_state)
# ---------------------------------------------------------------------------
if "report_dict" not in st.session_state or run_clicked:
    req = PipelineRequest(
        n_rows=int(n_rows),
        seed=int(seed),
        use_existing_data=(int(n_rows) == 300 and int(seed) == 42),
        llm_mode=llm_mode,
        confidence_threshold=float(confidence_threshold),
        ollama_model=ollama_model,
        ollama_host=ollama_host,
        ollama_api_key=ollama_api_key.strip() or None,
    )
    with st.spinner(f"Running {llm_mode.upper()} verification pipeline ({n_rows} rows)..."):
        report = run_pipeline(req, DATA_DIR, OUTPUTS_DIR)
        st.session_state["report_dict"] = asdict(report)

report_data = st.session_state["report_dict"]

# Live client-side confidence gate re-evaluation when slider moves
audit_rows = []
rule_count = 0
llm_count = 0
unres_count = 0
correct_total = 0
llm_correct = 0

for row in report_data["per_row_audit"]:
    r = dict(row)
    if r["engine"] != "rule":
        conf = float(r.get("confidence") or 0.0)
        cand_utr = r.get("matched_bank_utr")
        if not cand_utr and r.get("reasoning"):
            import re
            m = re.search(r"UTR_\d+", r["reasoning"])
            if m:
                cand_utr = m.group(0)
        if conf >= confidence_threshold and cand_utr:
            gt_set = [x for x in (r.get("ground_truth_match") or "").split("|") if x]
            r["match_type"] = "llm"
            r["engine"] = "llm"
            r["matched_bank_utr"] = cand_utr
            r["exception_category"] = None
            r["matches_ground_truth"] = cand_utr in gt_set
        else:
            is_orphan = not r.get("ground_truth_match")
            r["match_type"] = "unresolved"
            r["engine"] = "unresolved"
            r["matched_bank_utr"] = None
            r["matched_gateway_payment_id"] = None
            r["exception_category"] = "low_confidence" if (conf > 0 and cand_utr) else (r.get("exception_category") or "no_llm_match")
            r["matches_ground_truth"] = is_orphan

    if r["match_type"] == "rule":
        rule_count += 1
    elif r["match_type"] == "llm":
        llm_count += 1
        if r.get("matches_ground_truth"):
            llm_correct += 1
    else:
        unres_count += 1

    if r.get("matches_ground_truth"):
        correct_total += 1
    audit_rows.append(r)

total_rows = len(audit_rows)
verified_rate = (correct_total / total_rows) if total_rows else 0.0
llm_precision = (llm_correct / llm_count) if llm_count > 0 else None
total_match_rate = (rule_count + llm_count) / total_rows if total_rows else 0.0
red_flag = (total_match_rate > 0.92) and (
    verified_rate < 0.85 or (llm_precision is not None and llm_precision < 0.80)
)

# ---------------------------------------------------------------------------
# 4-Column Statistical Balance Strip
# ---------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(
        f'<div class="stat-box"><div class="stat-label">STAGE 0 // TOTAL LEDGER</div>'
        f'<div class="stat-value">{total_rows}</div>'
        f'<div class="stat-label">Invariant: {rule_count + llm_count + unres_count} = {total_rows} [OK]</div></div>',
        unsafe_allow_html=True,
    )
with c2:
    st.markdown(
        f'<div class="stat-box"><div class="stat-label">STAGE 1 // RULE PROVEN</div>'
        f'<div class="stat-value" style="color:#046A38;">{rule_count} ({rule_count/total_rows*100:.1f}%)</div>'
        f'<div class="stat-label">±₹0.01 · ±3 Days · Conf = 1.00</div></div>',
        unsafe_allow_html=True,
    )
with c3:
    st.markdown(
        f'<div class="stat-box"><div class="stat-label">STAGE 2 // LLM ESCALATED (≥ {confidence_threshold:.2f})</div>'
        f'<div class="stat-value" style="color:#1B365D;">{llm_count} ({llm_count/total_rows*100:.1f}%)</div>'
        f'<div class="stat-label">Engine: {llm_mode.upper()} ({ollama_model if llm_mode=="ollama" else "local"})</div></div>',
        unsafe_allow_html=True,
    )
with c4:
    st.markdown(
        f'<div class="stat-box"><div class="stat-label">STAGE 3 // HARD-GATED EXCEPTIONS</div>'
        f'<div class="stat-value" style="color:#D9381E;">{unres_count} ({unres_count/total_rows*100:.1f}%)</div>'
        f'<div class="stat-label">Halted below τ = {confidence_threshold:.2f}</div></div>',
        unsafe_allow_html=True,
    )

st.write("")

# ---------------------------------------------------------------------------
# Ground-Truth Answer Key Verification & 7 Noise Archetypes
# ---------------------------------------------------------------------------
left_col, right_col = st.columns([1, 1.4])
with left_col:
    st.subheader("Ground-Truth Answer Key Verification")
    flag_text = "🚨 RED FLAG: TRUE (SUSPICIOUS OVER-MATCHING)" if red_flag else "✅ RED FLAG: FALSE // VERIFIED"
    st.markdown(f"**Audit Status:** `{flag_text}`")
    m1, m2 = st.columns(2)
    m1.metric("Verified Accuracy vs Key", f"{verified_rate*100:.1f}%", f"{correct_total}/{total_rows} rows")
    m2.metric(
        "LLM Escalation Precision",
        f"{llm_precision*100:.1f}%" if llm_precision is not None else "N/A (0 LLM rows)",
        f"{llm_correct}/{llm_count} verified" if llm_count else "Rule-only baseline",
    )
    st.download_button(
        "⬇ Download Full Audit Report JSON",
        data=json.dumps(report_data, indent=2),
        file_name=f"reconciliation_audit_{report_data['run_id']}.json",
        mime="application/json",
    )

with right_col:
    st.subheader("Seven Synthetic Discrepancy Archetypes")
    noise_rows = []
    for ntype, stats in report_data["noise_type_summary"].items():
        noise_rows.append(
            {
                "Noise Archetype": ntype,
                "Total Rows": stats["total"],
                "Matched": stats["matched"],
                "Unresolved": stats["unresolved"],
                "Verified vs Answer Key": stats["correctly_matched"],
            }
        )
    st.dataframe(pd.DataFrame(noise_rows), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Three-Leg Settlement Audit Trail Table
# ---------------------------------------------------------------------------
st.subheader("Three-Leg Settlement Audit Trail: [Internal Ledger] ↔ [Gateway] ↔ [Bank UTR]")
f1, f2, f3 = st.columns([1, 1, 2])
with f1:
    engine_filter = st.selectbox("Filter by Resolution Engine", ["all", "unresolved", "llm", "rule"])
with f2:
    noise_options = ["all"] + list(report_data["noise_type_summary"].keys())
    noise_filter = st.selectbox("Filter by Noise Archetype", noise_options)
with f3:
    search_query = st.text_input("Search TXN_ID, UTR, PAY_ID, or Reasoning", "")

df = pd.DataFrame(audit_rows)
if engine_filter != "all":
    df = df[df["match_type"] == engine_filter]
if noise_filter != "all":
    df = df[df["noise_type"] == noise_filter]
if search_query.strip():
    q = search_query.strip().lower()
    df = df[
        df["ledger_txn_id"].fillna("").str.lower().str.contains(q)
        | df["matched_bank_utr"].fillna("").str.lower().str.contains(q)
        | df["matched_gateway_payment_id"].fillna("").str.lower().str.contains(q)
        | df["reasoning"].fillna("").str.lower().str.contains(q)
    ]

display_cols = [
    "ledger_txn_id",
    "noise_type",
    "matched_gateway_payment_id",
    "matched_bank_utr",
    "ground_truth_match",
    "match_type",
    "confidence",
    "exception_category",
    "matches_ground_truth",
    "reasoning",
]
st.dataframe(df[display_cols], use_container_width=True, hide_index=True, height=440)
