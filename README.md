# reconciliation-copilot

> AI-powered payment reconciliation engine built for the Razorpay Buildathon.

## Project Structure

```
reconciliation-copilot/
├── README.md
├── requirements.txt
├── docs/PRD.md
├── src/
│   ├── config.py
│   ├── data_gen/generate_synthetic_data.py
│   ├── matching/
│   │   ├── deterministic_matcher.py
│   │   └── llm_escalator.py
│   ├── reporting/audit_report.py
│   └── api/main.py
├── data/
├── tests/
└── outputs/
```

## Setup

```bash
pip install -r requirements.txt
```

## Running the API

```bash
uvicorn src.api.main:app --reload
```

## Running Tests

```bash
pytest tests/
```
