"""
Reconciliation Copilot — standalone CLI runner.

Runs the full 4-agent pipeline (data generation -> deterministic rule matcher
-> LLM escalation -> ground-truth audit report) and prints the summary.

Usage:
    python run_demo.py [--mode heuristic|offline|ollama] [--rows N] [--seed S] [--fresh]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.api.pipeline import PipelineRequest, run_pipeline

DATA_DIR    = Path("data")
OUTPUTS_DIR = Path("outputs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconciliation Copilot — 4-Agent Pipeline Runner")
    parser.add_argument(
        "--mode",
        choices=["offline", "heuristic", "ollama"],
        default="heuristic",
        help="Escalation mode: 'heuristic' (default simulator), 'ollama' (live gemma4/qwen3.1), or 'offline' (rules only)",
    )
    parser.add_argument("--model", default="gemma4", choices=["gemma4", "qwen3.1"], help="Ollama model (default: gemma4)")
    parser.add_argument("--rows",  type=int, default=300, help="Number of ledger rows (default: 300)")
    parser.add_argument("--seed",  type=int, default=42,  help="Random seed (default: 42)")
    parser.add_argument("--fresh", action="store_true",   help="Force re-generate synthetic data")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Reconciliation Copilot  |  4-Agent Verification Pipeline")
    print("=" * 60)
    print(f"  Rows : {args.rows}  |  Seed : {args.seed}  |  Mode : {args.mode}")
    print("=" * 60)
    print()

    req = PipelineRequest(
        n_rows=args.rows,
        seed=args.seed,
        use_existing_data=not args.fresh,
        llm_mode=args.mode,
        ollama_model=args.model,
    )

    report = run_pipeline(req, DATA_DIR, OUTPUTS_DIR)

    out_path = OUTPUTS_DIR / f"report_{report.run_id}.json"
    print(f"\nJSON audit report saved to: {out_path}")
    print("\nTo launch the interactive Streamlit web application:")
    print("  streamlit run streamlit_app.py")
    print()


if __name__ == "__main__":
    main()
