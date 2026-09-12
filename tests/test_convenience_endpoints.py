"""
DIYA tests/test_convenience_endpoints.py — Phase C.6: geocode, overview,
dispatch, evidence, export, methodology.

Geocode tests mock the Nominatim HTTP call (core.geocode.requests.get) --
never hit the real network. Every other test hits the real app (api.main.app)
via TestClient, which owns the real, production sites/registry.json; any
test that creates a site deletes it again before returning.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient

import api.main as api_main
from core.types import RunResult

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "data" / "processed" / "runs"

client = TestClient(api_main.app)


def _new_site_body(**overrides) -> dict:
    body = dict(
        display_name="Convenience Endpoint Test Site",
        lat=-33.9,
        lon=18.4,
        pv_capacity_kwp=10.0,
        battery_capacity_kwh=30.0,
    )
    body.update(overrides)
    return body


@pytest.fixture
def temp_site():
    resp = client.post("/api/sites", json=_new_site_body())
    assert resp.status_code == 201
    site_id = resp.json()["site_id"]
    yield site_id
    client.delete(f"/api/sites/{site_id}")


# ---------------------------------------------------------------------------
# Geocode
# ---------------------------------------------------------------------------


def _fake_response(json_body, status_ok=True):
    resp = MagicMock()
    if status_ok:
        resp.raise_for_status = lambda: None
    else:
        def _raise():
            raise requests.HTTPError("bad status")

        resp.raise_for_status = _raise
    resp.json = lambda: json_body
    return resp


def test_geocode_forward_success(monkeypatch):
    fake = _fake_response([{"lat": "23.2419", "lon": "69.6669", "display_name": "Bhuj, Kutch, Gujarat, India"}])
    monkeypatch.setattr("core.geocode.requests.get", lambda *a, **k: fake)

    resp = client.post("/api/geocode", json={"query": "Bhuj, Gujarat"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["lat"] == pytest.approx(23.2419)
    assert data["lon"] == pytest.approx(69.6669)
    assert data["display_name"] == "Bhuj, Kutch, Gujarat, India"


def test_geocode_forward_not_found(monkeypatch):
    fake = _fake_response([])
    monkeypatch.setattr("core.geocode.requests.get", lambda *a, **k: fake)

    resp = client.post("/api/geocode", json={"query": "definitely-not-a-real-place-xyz"})

    assert resp.status_code == 404
    assert "no geocoding result" in resp.json()["detail"]


def test_geocode_forward_service_unreachable(monkeypatch):
    def _raise(*a, **k):
        raise requests.ConnectionError("simulated network failure")

    monkeypatch.setattr("core.geocode.requests.get", _raise)

    # A query string not used by any other test in this file -- the
    # in-memory cache is real, process-lifetime state, so reusing a query
    # another test already resolved would serve the cached result instead
    # of exercising this failure path.
    resp = client.post("/api/geocode", json={"query": "a query never geocoded elsewhere in this test file"})

    assert resp.status_code == 502
    assert "Nominatim" in resp.json()["detail"]


def test_geocode_reverse_success(monkeypatch):
    fake = _fake_response({"display_name": "Bhuj, Kutch, Gujarat, India"})
    monkeypatch.setattr("core.geocode.requests.get", lambda *a, **k: fake)

    resp = client.post("/api/geocode/reverse", json={"lat": 23.2419, "lon": 69.6669})

    assert resp.status_code == 200
    assert resp.json() == {"display_name": "Bhuj, Kutch, Gujarat, India"}


def test_geocode_reverse_not_found(monkeypatch):
    fake = _fake_response({"error": "Unable to geocode"})
    monkeypatch.setattr("core.geocode.requests.get", lambda *a, **k: fake)

    resp = client.post("/api/geocode/reverse", json={"lat": 0.0, "lon": 0.0})

    assert resp.status_code == 404


def test_geocode_reverse_service_unreachable(monkeypatch):
    def _raise(*a, **k):
        raise requests.Timeout("simulated timeout")

    monkeypatch.setattr("core.geocode.requests.get", _raise)

    # Distinct coordinates from any other test in this file -- see the
    # matching comment on test_geocode_forward_service_unreachable.
    resp = client.post("/api/geocode/reverse", json={"lat": 1.23456, "lon": 7.891011})

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def test_overview_khavda_is_full():
    resp = client.get("/api/sites/khavda/overview")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "full"
    assert data["has_precomputed_runs"] is True
    assert data["site"]["site_id"] == "khavda"
    assert "current_step" in data
    assert data["current_step"]["t"]


def test_overview_new_site_is_live_only(temp_site):
    resp = client.get(f"/api/sites/{temp_site}/overview")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "live_only"
    assert data["has_precomputed_runs"] is False


def test_overview_unknown_site_404():
    resp = client.get("/api/sites/does_not_exist_xyz/overview")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_dispatch_khavda_range_168_returns_168_steps():
    resp = client.get("/api/sites/khavda/dispatch?range=168")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["steps"]) == 168
    assert data["source"] == "precomputed"
    assert data["policy"] == "mpc"


def test_dispatch_khavda_range_72_returns_first_72_steps():
    resp_168 = client.get("/api/sites/khavda/dispatch?range=168")
    resp_72 = client.get("/api/sites/khavda/dispatch?range=72")

    assert resp_72.status_code == 200
    data_72 = resp_72.json()
    data_168 = resp_168.json()
    assert len(data_72["steps"]) == 72
    assert data_72["scenario_id"] == data_168["scenario_id"]
    assert data_72["steps"] == data_168["steps"][:72]


def test_dispatch_new_site_range_72_is_live(temp_site):
    resp = client.get(f"/api/sites/{temp_site}/dispatch?range=72")

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "live"
    assert data["scenario_id"] == "LIVE"
    assert len(data["steps"]) == 72


def test_dispatch_new_site_range_168_returns_expected_404(temp_site):
    resp = client.get(f"/api/sites/{temp_site}/dispatch?range=168")

    assert resp.status_code == 404
    assert resp.json()["detail"] == (
        "Full week analysis requires historical scenario data, not yet available for this site"
    )


def test_dispatch_invalid_range_422():
    resp = client.get("/api/sites/khavda/dispatch?range=24")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_evidence_khavda_s2_delta_matches_independent_computation():
    resp = client.get("/api/sites/khavda/evidence?scenario=S2")
    assert resp.status_code == 200
    data = resp.json()

    rule_based = json.loads((RUNS_DIR / "S2_rule_based.json").read_text(encoding="utf-8"))
    mpc = json.loads((RUNS_DIR / "S2_mpc.json").read_text(encoding="utf-8"))

    expected_diesel_delta = rule_based["kpi"]["diesel_l"] - mpc["kpi"]["diesel_l"]
    assert data["delta"]["diesel_l"]["raw"] == pytest.approx(expected_diesel_delta, rel=1e-9)
    assert data["delta"]["diesel_l"]["avoided"] == pytest.approx(max(expected_diesel_delta, 0.0), rel=1e-9)

    expected_cost_delta = rule_based["kpi"]["cost_total_inr"] - mpc["kpi"]["cost_total_inr"]
    assert data["delta"]["cost_total_inr"]["raw"] == pytest.approx(expected_cost_delta, rel=1e-9)

    expected_co2_delta = rule_based["kpi"]["co2_kg"] - mpc["kpi"]["co2_kg"]
    assert data["delta"]["co2_kg"]["raw"] == pytest.approx(expected_co2_delta, rel=1e-9)

    assert data["description"] is not None and "HERO SCENARIO" in data["description"]
    assert data["rule_based"]["policy"] == "rule_based"
    assert data["mpc"]["policy"] == "mpc"
    assert data["diesel_only"]["policy"] == "diesel_only"
    assert data["perfect_foresight"]["policy"] == "perfect_foresight"


def test_evidence_new_site_no_runs_404(temp_site):
    resp = client.get(f"/api/sites/{temp_site}/evidence")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_csv_has_correct_headers_and_rows():
    resp = client.get("/api/sites/khavda/export?scenario=S2&policy=mpc&format=csv")

    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-type"].startswith("text/csv")

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    header = rows[0]
    assert header == list(RunResult.model_fields["steps"].annotation.__args__[0].model_fields.keys())
    assert len(rows) - 1 == 168


def test_export_json_matches_run_steps():
    resp = client.get("/api/sites/khavda/export?scenario=S2&policy=mpc&format=json")

    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    steps = json.loads(resp.text)

    on_disk = RunResult.model_validate_json((RUNS_DIR / "S2_mpc.json").read_text(encoding="utf-8"))
    assert steps == [json.loads(s.model_dump_json()) for s in on_disk.steps]


def test_export_missing_run_404():
    resp = client.get("/api/sites/khavda/export?scenario=S2&policy=does_not_exist&format=csv")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Methodology
# ---------------------------------------------------------------------------


def test_methodology_has_three_categories_and_is_stable():
    resp1 = client.get("/api/methodology")
    resp2 = client.get("/api/methodology")

    assert resp1.status_code == 200
    assert resp1.json() == resp2.json()

    data = resp1.json()
    labels = [c["label"] for c in data["categories"]]
    assert labels == ["Measured / Live", "Public Reference", "Engineering Assumption"]
    assert "load_note" in data
    assert "synthetic" in data["load_note"].lower()
