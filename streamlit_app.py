"""
Reconciliation Copilot — Explainable Streamlit App
Course Submission: Foundations of Agentic AI

Run locally:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from src.api.pipeline import PipelineRequest, run_pipeline
from src.config import EscalatorConfig
from src.matching.deterministic_matcher import (
    DeterministicMatcher,
    load_bank,
    load_gateway,
    load_ledger,
)
from src.matching.llm_escalator import LLMEscalator, OllamaLLMClient

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUTS_DIR = BASE_DIR / "outputs"

st.set_page_config(
    page_title="Reconciliation Copilot — Foundations of Agentic AI",
    page_icon="⚖️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar — Simple, Explainable Controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Agent Controls")

    engine_choice = st.radio(
        "1. Select Escalation Mode",
        options=[
            "Instant Agent Simulator (Fast Demo)",
            "Live Ollama Cloud (gemma4 / qwen3.1)",
            "Rule-Only Baseline (No AI)",
        ],
        index=0,
        help="Instant Simulator runs in <1s; Live Ollama Cloud calls real gemma4/qwen3.1 over the API.",
    )
    mode_map = {
        "Instant Agent Simulator (Fast Demo)": "heuristic",
        "Live Ollama Cloud (gemma4 / qwen3.1)": "ollama",
        "Rule-Only Baseline (No AI)": "offline",
    }
    llm_mode = mode_map[engine_choice]

    ollama_model = st.selectbox(
        "2. Ollama Cloud Model (Free Tier)",
        options=["gemma4", "qwen3.1"],
        index=0,
        help="gemma4 runs natively on Ollama Cloud Free Tier; qwen3.1 auto-falls back to gemma4 if unavailable.",
    )

    default_key = os.environ.get("OLLAMA_API_KEY", OllamaLLMClient.DEFAULT_CLOUD_KEY)
    if (BASE_DIR / ".streamlit" / "secrets.toml").exists():
        try:
            default_key = st.secrets.get("OLLAMA_API_KEY", default_key)
        except Exception:
            pass

    ollama_api_key = st.text_input(
        "Ollama API Key",
        value=default_key,
        type="password",
        help="Pre-configured with your Ollama Cloud Free Tier key.",
    )

    confidence_threshold = st.slider(
        "3. Code Confidence Gate (τ)",
        min_value=0.10,
        max_value=0.99,
        value=0.70,
        step=0.05,
        help="Any AI match below τ is blocked in Python code and sent to the human exception queue.",
    )

    run_btn = st.button("▶ Run 300-Row Reconciliation", type="primary", width="stretch")

# ---------------------------------------------------------------------------
# Header — Clear Problem & Solution Statement
# ---------------------------------------------------------------------------
st.title("⚖️ Reconciliation Copilot")
st.caption("Foundations of Agentic AI — Neuro-Symbolic Financial Verification Agent")

st.info(
    "**Core Idea:** Pure LLMs hallucinate on financial spreadsheets. This project uses a **2-stage hybrid agent**: "
    "**Stage 1 (Deterministic Rules)** proves 89% of transactions with exact math, and **Stage 2 (Ollama LLM + 0.70 Code Gate)** "
    "resolves rounding drift while refusing to guess on missing ('orphan') transactions."
)

# ---------------------------------------------------------------------------
# Run Pipeline (Cached in Session State; auto-refreshes when mode changes)
# ---------------------------------------------------------------------------
mode_changed = st.session_state.get("active_llm_mode") != llm_mode
if "report_dict" not in st.session_state or run_btn or (mode_changed and llm_mode != "ollama"):
    req = PipelineRequest(
        n_rows=300,
        seed=42,
        use_existing_data=True,
        llm_mode=llm_mode,
        confidence_threshold=float(confidence_threshold),
        ollama_model=ollama_model,
        ollama_host="https://ollama.com/v1",
        ollama_api_key=ollama_api_key.strip() or OllamaLLMClient.DEFAULT_CLOUD_KEY,
        llm_delay_ms=200,
    )
    with st.spinner(f"Running reconciliation ({engine_choice})..."):
        report = run_pipeline(req, DATA_DIR, OUTPUTS_DIR)
        st.session_state["report_dict"] = asdict(report)
        st.session_state["active_llm_mode"] = llm_mode

report_data = st.session_state["report_dict"]

# Re-apply confidence gate slider dynamically so metrics, breakdown table, and audit trail stay in sync
rows = []
rule_matched = 0
llm_matched = 0
unresolved = 0
verified_correct = 0
llm_correct = 0
noise_summary: dict[str, dict[str, int]] = {}

for orig in report_data["per_row_audit"]:
    r = dict(orig)
    if r["engine"] != "rule":
        conf = float(r.get("confidence") or 0.0)
        utr = r.get("matched_bank_utr")
        if not utr and r.get("reasoning"):
            m = re.search(r"UTR_\d+", r["reasoning"])
            if m:
                utr = m.group(0)
        if conf >= confidence_threshold and utr:
            gt_list = [x for x in (r.get("ground_truth_match") or "").split("|") if x]
            r["match_type"] = "llm"
            r["matched_bank_utr"] = utr
            r["matches_ground_truth"] = utr in gt_list
            r["exception_category"] = None
        else:
            r["match_type"] = "unresolved"
            r["matched_bank_utr"] = None
            r["matched_gateway_payment_id"] = None
            r["exception_category"] = (
                "low_confidence" if (conf > 0 and utr) else (r.get("exception_category") or "no_llm_match")
            )
            r["matches_ground_truth"] = not bool(r.get("ground_truth_match"))

    if r["match_type"] == "rule":
        rule_matched += 1
    elif r["match_type"] == "llm":
        llm_matched += 1
        if r.get("matches_ground_truth"):
            llm_correct += 1
    else:
        unresolved += 1

    if r.get("matches_ground_truth"):
        verified_correct += 1

    ntype = r.get("noise_type", "unknown")
    bucket = noise_summary.setdefault(
        ntype, {"total": 0, "matched": 0, "unresolved": 0, "correctly_matched": 0}
    )
    bucket["total"] += 1
    if r["match_type"] in ("rule", "llm"):
        bucket["matched"] += 1
    else:
        bucket["unresolved"] += 1
    if r.get("matches_ground_truth"):
        bucket["correctly_matched"] += 1

    rows.append(r)

total = len(rows)
llm_precision = (llm_correct / llm_matched) if llm_matched > 0 else None
total_match_rate = (rule_matched + llm_matched) / total if total else 0.0
red_flag = (total_match_rate > 0.70) and (llm_precision is not None and llm_precision < 0.70)

# ---------------------------------------------------------------------------
# Three Explainable Tabs
# ---------------------------------------------------------------------------
tab_dashboard, tab_live_ollama, tab_explain = st.tabs([
    "📊 1. Reconciliation Results",
    "🧪 2. Live Ollama (gemma4 / qwen3.1) Tester",
    "📖 3. How the 4 Agents Work (Viva Guide)",
])

with tab_dashboard:
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Ledger Transactions", f"{total}", "100% Accounted")
    k2.metric("Agent 1: Rule-Matched", f"{rule_matched} ({rule_matched/total*100:.1f}%)", "Exact Math (±₹0.01)")
    k3.metric(
        f"Agent 2: AI-Matched (≥ {confidence_threshold:.2f})",
        f"{llm_matched} ({llm_matched/total*100:.1f}%)",
        f"Mode: {st.session_state.get('active_llm_mode', llm_mode).upper()}",
    )
    k4.metric("Flagged Exceptions (< Gate)", f"{unresolved} ({unresolved/total*100:.1f}%)", "Sent to Human Review")

    st.success(
        f"**Ground-Truth Answer Key Score:** **{verified_correct} / {total} ({verified_correct/total*100:.1f}%)** "
        f"verified against `data/answer_key.csv` | **Audit Red Flag:** `{red_flag}`"
    )

    col_a, col_b = st.columns([1.1, 1.9])
    with col_a:
        st.subheader("Breakdown by Discrepancy Type")
        noise_table = [
            {
                "Discrepancy Type": k,
                "Total": v["total"],
                "Matched": v["matched"],
                "Unresolved": v["unresolved"],
                "Correct vs Key": v["correctly_matched"],
            }
            for k, v in sorted(noise_summary.items())
        ]
        st.dataframe(pd.DataFrame(noise_table), width="stretch", hide_index=True)

        st.download_button(
            "⬇ Download Audit Report (JSON)",
            data=json.dumps(report_data, indent=2),
            file_name=f"audit_report_{report_data['run_id']}.json",
            mime="application/json",
            width="stretch",
        )

    with col_b:
        st.subheader("Per-Transaction Audit Trail")
        filter_choice = st.selectbox(
            "Filter Rows",
            ["All (300 rows)", "Only AI-Matched (Rounding Drift)", "Only Unresolved Exceptions (Orphans)", "Only Rule-Matched"],
        )
        df = pd.DataFrame(rows)
        if "AI-Matched" in filter_choice:
            df = df[df["match_type"] == "llm"]
        elif "Unresolved" in filter_choice:
            df = df[df["match_type"] == "unresolved"]
        elif "Rule-Matched" in filter_choice:
            df = df[df["match_type"] == "rule"]

        display_cols = [
            "txn_id",
            "noise_type",
            "matched_gateway_payment_id",
            "matched_bank_utr",
            "match_type",
            "confidence",
            "matches_ground_truth",
            "reasoning",
        ]
        st.dataframe(
            df[display_cols] if not df.empty else pd.DataFrame(columns=display_cols),
            width="stretch",
            hide_index=True,
            height=340,
        )

with tab_live_ollama:
    st.subheader("🧪 Test Live Ollama Cloud (`gemma4` / `qwen3.1`) on Any Unmatched Row")
    st.write(
        "Instead of waiting for all 33 unmatched rows over the network during a live presentation, "
        "pick any single unmatched transaction below and click **Ask Ollama Live Now** to watch the real LLM reason in ~2 seconds."
    )

    ledger = load_ledger(DATA_DIR / "internal_ledger.csv")
    bank = load_bank(DATA_DIR / "bank_settlement.csv")
    gateway = load_gateway(DATA_DIR / "gateway_settlement.csv")
    _, unmatched_rows = DeterministicMatcher().match(ledger, bank, gateway)

    unmatched_options = {
        f"{r.txn_id} | Order: {r.order_id} | Amount: ₹{r.amount} | Date: {r.timestamp.strftime('%Y-%m-%d')}": r
        for r in unmatched_rows
    }
    selected_label = st.selectbox(
        "Choose an Unmatched Ledger Row (from the 33 rows Rules couldn't solve):",
        list(unmatched_options.keys()),
    )
    selected_row = unmatched_options[selected_label]

    if st.button("⚡ Ask Ollama Live Now", type="primary"):
        client = OllamaLLMClient(
            model=ollama_model,
            api_key=ollama_api_key.strip() or OllamaLLMClient.DEFAULT_CLOUD_KEY,
        )
        esc = LLMEscalator(
            client=client,
            config=EscalatorConfig(model=ollama_model, confidence_threshold=confidence_threshold),
        )
        with st.spinner(f"Calling Ollama Cloud ({ollama_model})..."):
            result = esc.escalate([selected_row], bank, gateway)[0]

        c_left, c_right = st.columns(2)
        with c_left:
            st.markdown("#### 1. Ollama LLM Verdict")
            st.json({
                "ledger_txn_id": result.ledger_txn_id,
                "model_requested": ollama_model,
                "proposed_bank_utr": result.matched_bank_utr,
                "confidence": result.confidence,
                "confidence_gate_threshold": confidence_threshold,
                "final_status": result.match_type,
                "exception_category": result.exception_category,
            })
        with c_right:
            st.markdown("#### 2. Natural-Language Reasoning")
            st.info(result.reasoning or "No reasoning returned.")
            if result.match_type == "llm":
                st.success(
                    f"PASSED Code Gate ({result.confidence:.2f} ≥ {confidence_threshold:.2f}) "
                    f"→ Committed as Match `{result.matched_bank_utr}`"
                )
            else:
                st.warning(
                    f"BLOCKED by Code Gate → Routed to Human Exception Queue (`{result.exception_category}`)"
                )

with tab_explain:
    st.subheader("📖 How to Explain This Project in 60 Seconds")
    st.markdown(
        """
        ### 1. What Problem Does It Solve?
        When a customer pays online, **3 separate systems** record the money:
        1. **Internal Ledger** (our app database)
        2. **Payment Gateway** (settlement processor)
        3. **Bank Statement** (actual bank UTR credit)

        In real life, these 3 files rarely match 100% because of **rounding drift** (e.g., ₹0.50 difference), **missing bank UTR references**, **timing delays** (+1 to +3 days), **duplicates**, **partial refunds**, or **orphan transactions** (captured in app, never settled in bank).

        ---
        ### 2. Our 4-Agent Pipeline
        | Stage | Component | What It Does | Why It Matters |
        | :--- | :--- | :--- | :--- |
        | **Stage 1** | **Synthetic Data Generator** (`generate_synthetic_data.py`) | Creates 300 realistic transactions across 7 discrepancy types + `answer_key.csv`. | Gives us a 100% objective ground truth to grade the AI. |
        | **Stage 2** | **Deterministic Rule Agent** (`deterministic_matcher.py`) | Matches exact Order ID, date within ±3 days, and amount within ±₹0.01. | Solves **267 / 300 rows (89%)** in milliseconds with **zero AI cost** and **zero hallucination**. |
        | **Stage 3** | **Ollama LLM Escalation Agent** (`llm_escalator.py`) | Sends only the **33 unmatched rows** + top-5 close bank candidates to **Ollama (`gemma4` / `qwen3.1`)**. | Resolves the **30 rounding-drift rows** by reading the Order ID inside the bank narration text. |
        | **Stage 4** | **Code Gate + Audit Reporter** (`audit_report.py`) | Enforces `confidence >= 0.70` in Python code and grades all 300 rows against `answer_key.csv`. | Correctly blocks the **3 true orphan rows** from being falsely matched, achieving **100% precision**. |
        """
    )
