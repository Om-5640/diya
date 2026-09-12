"""
DIYA tests/test_api_live.py — Phase 4 live-endpoint tests: /api/solve_live
(with a forced fallback-chain path) and /api/resolve (KPI sanity + a
reliability-weight sensitivity check), plus malformed-body 422 checks for
both.

RUNTIME NOTE: this file measures ~230s, not the originally targeted 30s.
/api/resolve runs a full 168-hour rolling MPC (168 sequential solve_dispatch
calls); each already converges to Optimal in ~200-400ms, but PULP_CBC_CMD's
per-call subprocess spawn (shelling out to cbc.exe 168 times) costs ~60-65s
total regardless of time_limit_s -- confirmed by direct measurement at both
1.5s and 3.0s (identical ~63s total, zero solves ever hitting the limit).
This is inherent to core/milp.py's existing solver invocation, which this
phase does not modify; three resolve-touching tests below unavoidably cost
~3x that. Accepted as the real cost of testing this endpoint honestly
rather than only via a mock.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as api_main

REPO_ROOT = Path(__file__).resolve().parents[1]
S1_MPC_PATH = REPO_ROOT / "data" / "processed" / "runs" / "S1_mpc.json"

client = TestClient(api_main.app)

KHAVDA_COORDS = {"lat": 23.8443, "lon": 69.7317, "soc_pct": 60.0}


def test_solve_live_returns_valid_run_within_5s():
    start = time.perf_counter()
    resp = client.post("/api/solve_live", json=KHAVDA_COORDS)
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    assert elapsed < 5.0, f"solve_live took {elapsed:.2f}s, expected < 5s"

    data = resp.json()
    assert data["scenario_id"] == "LIVE"
    assert data["policy"] == "mpc"
    assert len(data["steps"]) == 72
    assert "kpi" in data
    assert "provenance" in data


def test_solve_live_falls_back_when_network_fails(monkeypatch):
    def _raise_network(*args, **kwargs):
        raise RuntimeError("simulated network failure")

    def _raise_no_cache(*args, **kwargs):
        raise FileNotFoundError("simulated: no cache present")

    # Force past both the live fetch AND the cache tier so the fallback
    # chain deterministically lands on scenario-replay, regardless of
    # whether a real live_cache.json happens to exist on disk already.
    monkeypatch.setattr(api_main, "fetch_forecast_live", _raise_network)
    monkeypatch.setattr(api_main, "load_cached_forecast", _raise_no_cache)

    resp = client.post("/api/solve_live", json=KHAVDA_COORDS)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["steps"]) == 72
    assert data["provenance"]["forecast_method"] == "scenario-replay-fallback"


def test_solve_live_rejects_malformed_body_with_422():
    resp = client.post("/api/solve_live", json={"lat": "not-a-number", "lon": 69.7317, "soc_pct": 60})
    assert resp.status_code == 422

    resp2 = client.post("/api/solve_live", json={"lat": 23.8443})  # missing lon/soc_pct
    assert resp2.status_code == 422


def test_resolve_default_weights_close_to_precomputed_s1_mpc():
    assert S1_MPC_PATH.exists(), f"missing {S1_MPC_PATH}; run scripts/precompute_runs.py first"
    precomputed = json.loads(S1_MPC_PATH.read_text(encoding="utf-8"))
    precomputed_kpi = precomputed["kpi"]

    body = {
        "scenario_id": "S1",
        "weights": {"cost": 1.0, "co2": 1.0, "reliability": 1.0},
        "k_uncertainty": 1.0,
        "diesel_price_inr_per_l": 92.5,
    }
    resp = client.post("/api/resolve", json=body)
    assert resp.status_code == 200
    data = resp.json()

    assert data["scenario_id"] == "S1"
    assert data["policy"] == "mpc"
    assert len(data["steps"]) == 168

    kpi = data["kpi"]
    assert kpi["cost_total_inr"] == pytest.approx(precomputed_kpi["cost_total_inr"], rel=0.05)
    assert kpi["diesel_l"] == pytest.approx(precomputed_kpi["diesel_l"], rel=0.05)


def test_resolve_higher_reliability_weight_never_increases_unserved_critical():
    base_body = {
        "scenario_id": "S2",
        "weights": {"cost": 1.0, "co2": 1.0, "reliability": 1.0},
        "k_uncertainty": 1.2,
        "diesel_price_inr_per_l": 92.5,
    }
    high_body = {**base_body, "weights": {"cost": 1.0, "co2": 1.0, "reliability": 2.0}}

    resp_base = client.post("/api/resolve", json=base_body)
    resp_high = client.post("/api/resolve", json=high_body)

    assert resp_base.status_code == 200
    assert resp_high.status_code == 200

    unserved_base = resp_base.json()["kpi"]["unserved_critical_kwh"]
    unserved_high = resp_high.json()["kpi"]["unserved_critical_kwh"]

    assert unserved_high <= unserved_base + 1e-6


def test_resolve_rejects_malformed_body_with_422():
    resp = client.post("/api/resolve", json={"scenario_id": "S1"})  # missing k_uncertainty/diesel_price
    assert resp.status_code == 422

    resp2 = client.post(
        "/api/resolve",
        json={
            "scenario_id": "S1",
            "weights": {"cost": "high"},  # wrong type
            "k_uncertainty": 1.0,
            "diesel_price_inr_per_l": 92.5,
        },
    )
    assert resp2.status_code == 422
