"""
API smoke tests — uses FastAPI's TestClient (no real server needed).

Scope: liveness, basic shape of /reconcile response, report retrieval, 404.
These are smoke tests, not exhaustive pipeline tests (those live in test_integration.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Override data paths BEFORE importing the app so env vars take effect
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _set_env():
    """Point the API at the real data/ and outputs/ directories that already exist."""
    os.environ["DATA_DIR"]    = str(Path("data").resolve())
    os.environ["OUTPUTS_DIR"] = str(Path("outputs").resolve())
    yield
    # Restore
    os.environ.pop("DATA_DIR", None)
    os.environ.pop("OUTPUTS_DIR", None)


@pytest.fixture(scope="module")
def client():
    # Import inside fixture so env vars are set first
    from src.api.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test 1 — /health returns 200 with expected schema
# ---------------------------------------------------------------------------

def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


# ---------------------------------------------------------------------------
# Test 2 — POST /reconcile returns 200 with expected top-level keys
# ---------------------------------------------------------------------------

def test_reconcile_smoke(client):
    """Full pipeline smoke test against real 300-row data (offline LLM mode)."""
    r = client.post("/reconcile", json={
        "use_existing_data": True,
        "llm_mode": "offline",
        "n_rows": 300,
        "seed": 42,
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"

    body = r.json()
    # Required top-level fields from ReconcileResponse / AuditReport
    for key in ("run_id", "total_rows", "rule_matched", "llm_matched",
                "unresolved", "rule_match_rate", "total_match_rate",
                "unresolved_rate", "verified_match_rate",
                "per_row_audit", "exception_breakdown", "noise_type_summary"):
        assert key in body, f"Missing key: {key}"

    assert body["total_rows"] == 300
    assert body["rule_matched"] == 267    # known from prior runs (seed=42)
    assert body["rule_matched"] + body["llm_matched"] + body["unresolved"] == 300
    assert len(body["per_row_audit"]) == 300
    # Offline mode: all unmatched rows become unresolved
    assert body["llm_matched"] == 0


# ---------------------------------------------------------------------------
# Test 3 — GET /report/{run_id} returns the stored report
# ---------------------------------------------------------------------------

def test_get_report_found(client):
    """Run reconcile, grab the run_id, fetch it back via GET /report/{run_id}."""
    post_r = client.post("/reconcile", json={"use_existing_data": True, "llm_mode": "offline"})
    assert post_r.status_code == 200
    run_id = post_r.json()["run_id"]

    get_r = client.get(f"/report/{run_id}")
    assert get_r.status_code == 200
    fetched = get_r.json()
    assert fetched["run_id"] == run_id
    assert fetched["total_rows"] == 300


# ---------------------------------------------------------------------------
# Test 4 — GET /report/{nonexistent} returns 404
# ---------------------------------------------------------------------------

def test_get_report_not_found(client):
    r = client.get("/report/DOESNOTEXIST_000000")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()
