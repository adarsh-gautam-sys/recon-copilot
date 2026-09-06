"""
Reconciliation Copilot — standalone demo script.

Runs the full pipeline (data generation → deterministic match → LLM escalation
in offline mode → audit report) and prints the formatted summary.

Usage:
    python run_demo.py [--rows N] [--seed S] [--fresh]

Options:
    --rows N   Number of ledger rows to generate (default: 300)
    --seed S   Random seed for reproducibility (default: 42)
    --fresh    Force re-generation of synthetic data even if CSVs already exist
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.api.pipeline import PipelineRequest, run_pipeline

DATA_DIR    = Path("data")
OUTPUTS_DIR = Path("outputs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconciliation Copilot — one-command demo")
    parser.add_argument("--rows",  type=int, default=300, help="Number of ledger rows (default: 300)")
    parser.add_argument("--seed",  type=int, default=42,  help="Random seed (default: 42)")
    parser.add_argument("--fresh", action="store_true",   help="Force re-generate synthetic data")
    args = parser.parse_args()

    print()
    print("=" * 55)
    print("  Reconciliation Copilot  |  One-Command Demo")
    print("=" * 55)
    print(f"  Rows : {args.rows}  |  Seed : {args.seed}  |  LLM : offline")
    print("=" * 55)
    print()

    req = PipelineRequest(
        n_rows=args.rows,
        seed=args.seed,
        use_existing_data=not args.fresh,
        llm_mode="offline",
    )

    report = run_pipeline(req, DATA_DIR, OUTPUTS_DIR)

    out_path = OUTPUTS_DIR / f"report_{report.run_id}.json"
    print(f"\nJSON report saved to: {out_path}")
    print("\nTo enable live LLM matching, set OPENROUTER_API_KEY and run:")
    print("  python run_llm_escalation.py --model z-ai/glm-5.2:free --delay-ms 3000")
    print()


if __name__ == "__main__":
    main()
