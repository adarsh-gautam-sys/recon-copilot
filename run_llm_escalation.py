"""
Live LLM escalation run against real unmatched rows.
Uses Ollama Cloud Free Tier (or local Ollama daemon) with qwen3.1 or gemma4.

Usage:
  $env:OLLAMA_API_KEY = 'your-ollama-cloud-key'   # optional if running local ollama serve
  python run_llm_escalation.py [--model qwen3.1] [--model gemma4] [--probe-only]
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from src.config import EscalatorConfig
from src.matching.deterministic_matcher import (
    DeterministicMatcher, load_bank, load_gateway, load_ledger
)
from src.matching.llm_escalator import LLMEscalator, OllamaLLMClient

DATA_DIR = Path("data")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Ollama LLM escalation on unmatched rows.")
    parser.add_argument("--model", default="qwen3.1", choices=["qwen3.1", "gemma4"],
                        help="Ollama model (qwen3.1 or gemma4)")
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "https://ollama.com/v1"),
                        help="Ollama API host (Cloud free tier https://ollama.com/v1 or local http://localhost:11434/v1)")
    parser.add_argument("--probe-only", action="store_true",
                        help="Run only the 5-row probe, skip full run")
    parser.add_argument("--probe-n", type=int, default=5,
                        help="Number of rows in the probe batch (default: 5)")
    parser.add_argument("--delay-ms", type=int, default=500,
                        help="Milliseconds to wait between API calls (default: 500)")
    args = parser.parse_args()

    api_key = os.environ.get("OLLAMA_API_KEY")
    print(f"Engine: Ollama ({args.host}) | Model: {args.model}")

    # Load data
    ledger  = load_ledger(DATA_DIR / "internal_ledger.csv")
    bank    = load_bank(DATA_DIR / "bank_settlement.csv")
    gateway = load_gateway(DATA_DIR / "gateway_settlement.csv")

    # Deterministic matcher
    matcher = DeterministicMatcher()
    matched_rule, unmatched = matcher.match(ledger, bank, gateway)
    print(f"Deterministic matcher: {len(matched_rule)} rule-matched, {len(unmatched)} unmatched\n")

    # Build escalator
    client = OllamaLLMClient(model=args.model, api_key=api_key, base_url=args.host)
    cfg = EscalatorConfig(model=args.model, confidence_threshold=0.70, call_delay_seconds=args.delay_ms / 1000.0)
    escalator = LLMEscalator(client=client, config=cfg)

    # --- Probe batch ---
    n = min(args.probe_n, len(unmatched))
    print(f"--- PROBE: first {n} unmatched rows ---")
    probe = escalator.escalate(unmatched[:n], bank, gateway)
    for r in probe:
        print(
            f"  {r.ledger_txn_id}: match_type={r.match_type:12s} "
            f"conf={r.confidence:.2f}  cat={r.exception_category or '-':20s} "
            f"utr={r.matched_bank_utr or '(none)'}"
        )
        if r.reasoning:
            print(f"    reasoning: {r.reasoning[:120]}")
    print()

    if args.probe_only:
        return

    # --- Full run ---
    print(f"--- FULL RUN: all {len(unmatched)} unmatched rows ---")
    all_results = escalator.escalate(unmatched, bank, gateway)

    # Confidence score distribution
    buckets = {
        "0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0,
        "0.6-0.7": 0, "0.7-0.8": 0, "0.8-0.9": 0, "0.9-1.0": 0,
    }
    for r in all_results:
        c = r.confidence
        if   c < 0.2: buckets["0.0-0.2"] += 1
        elif c < 0.4: buckets["0.2-0.4"] += 1
        elif c < 0.6: buckets["0.4-0.6"] += 1
        elif c < 0.7: buckets["0.6-0.7"] += 1
        elif c < 0.8: buckets["0.7-0.8"] += 1
        elif c < 0.9: buckets["0.8-0.9"] += 1
        else:         buckets["0.9-1.0"] += 1

    print("\nConfidence score distribution:")
    for bucket, count in buckets.items():
        bar = "#" * count
        print(f"  [{bucket}] {count:3d}  {bar}")

    # Summary
    llm_matched = [r for r in all_results if r.match_type == "llm"]
    still_unresolved = [r for r in all_results if r.match_type == "unresolved"]
    by_cat: dict[str, int] = {}
    for r in still_unresolved:
        by_cat[r.exception_category or "unknown"] = by_cat.get(r.exception_category or "unknown", 0) + 1

    total = len(ledger)
    print(f"\nFinal pipeline summary (total ledger rows: {total})")
    print(f"  Rule-matched       : {len(matched_rule):3d}  ({len(matched_rule)/total:.1%})")
    print(f"  LLM-matched        : {len(llm_matched):3d}  ({len(llm_matched)/total:.1%})")
    print(f"  Unresolved         : {len(still_unresolved):3d}  ({len(still_unresolved)/total:.1%})")
    print(f"  Total matched      : {len(matched_rule)+len(llm_matched):3d}  ({(len(matched_rule)+len(llm_matched))/total:.1%})")
    print(f"\nUnresolved breakdown by exception_category:")
    for cat, cnt in sorted(by_cat.items()):
        print(f"  {cat}: {cnt}")

    # Save full results
    Path("outputs").mkdir(exist_ok=True)
    all_records = matched_rule + all_results
    out = [
        {
            "match_id": r.match_id,
            "ledger_txn_id": r.ledger_txn_id,
            "matched_bank_utr": r.matched_bank_utr,
            "matched_gateway_payment_id": r.matched_gateway_payment_id,
            "match_type": r.match_type,
            "confidence": r.confidence,
            "reasoning": r.reasoning,
            "exception_category": r.exception_category,
        }
        for r in all_records
    ]
    out_path = Path("outputs/llm_escalation_results.json")
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results saved to: {out_path}")


if __name__ == "__main__":
    main()
