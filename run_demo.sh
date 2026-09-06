#!/usr/bin/env bash
# run_demo.sh — one-command demo for Linux / macOS / WSL
# Usage: bash run_demo.sh [--rows N] [--seed S] [--fresh]

set -e

echo ""
echo "Installing dependencies..."
pip install -r requirements.txt -q

echo ""
echo "Running Reconciliation Copilot demo..."
python run_demo.py "$@"
