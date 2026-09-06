.DEFAULT_GOAL := help

.PHONY: help install data demo test serve

help:
	@echo ""
	@echo "Reconciliation Copilot — available targets"
	@echo "------------------------------------------"
	@echo "  make install   Install Python dependencies"
	@echo "  make data      Generate synthetic CSV data (300 rows, seed 42)"
	@echo "  make demo      Install deps + run full pipeline demo"
	@echo "  make test      Run the full pytest test suite"
	@echo "  make serve     Start the FastAPI dev server on localhost:8000"
	@echo ""

install:
	pip install -r requirements.txt

data:
	python -m src.data_gen.generate_synthetic_data --rows 300 --seed 42

demo: install
	python run_demo.py

test:
	python -m pytest tests/ -v

serve:
	uvicorn src.api.main:app --reload --port 8000
