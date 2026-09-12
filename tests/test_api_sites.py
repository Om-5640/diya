"""
DIYA tests/test_api_sites.py — Phase C: site-scoped API + legacy-route
zero-regression tests.

These hit the REAL app (api.main.app), which owns the real, production
sites/registry.json (api.main's module-level STORE = SiteStore()) --
unlike tests/test_site_store.py, which never touches it. Every test that
creates a site deletes it again before returning, so re-running this file
never leaves stray sites/site_*/ directories or registry entries behind.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from core.types import RunResult

client = TestClient(api_main.app)

KHAVDA_COORDS = {"lat": 23.8443, "lon": 69.7317, "soc_pct": 60.0}


def _new_site_body(**overrides) -> dict:
    body = dict(
        display_name="Pytest Test Site",
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
# Site management
# ---------------------------------------------------------------------------


def test_create_site_success(temp_site):
    resp = client.get(f"/api/sites/{temp_site}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["site_id"] == temp_site
    assert data["display_name"] == "Pytest Test Site"
    assert data["is_seed"] is False


def test_list_sites_includes_khavda_and_created_site(temp_site):
    resp = client.get("/api/sites")
    assert resp.status_code == 200
    ids = {s["site_id"] for s in resp.json()}
    assert "khavda" in ids
    assert temp_site in ids


def test_unknown_site_id_returns_404():
    resp = client.get("/api/sites/does_not_exist_12345")
    assert resp.status_code == 404


def test_delete_khavda_rejected():
    resp = client.delete("/api/sites/khavda")
    assert resp.status_code == 400
    resp2 = client.get("/api/sites/khavda")
    assert resp2.status_code == 200


def test_delete_new_site_succeeds_and_disappears():
    resp = client.post("/api/sites", json=_new_site_body(display_name="Delete Me"))
    assert resp.status_code == 201
    site_id = resp.json()["site_id"]

    del_resp = client.delete(f"/api/sites/{site_id}")
    assert del_resp.status_code == 204

    get_resp = client.get(f"/api/sites/{site_id}")
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# New site's runs endpoints: clean 404s, not 500s or fake empty 200s
# ---------------------------------------------------------------------------


def test_new_site_single_run_returns_clean_404(temp_site):
    resp = client.get(f"/api/sites/{temp_site}/runs/S1/mpc")
    assert resp.status_code == 404
    assert "no precomputed run" in resp.json()["detail"]


def test_new_site_runs_list_is_empty_not_error(temp_site):
    resp = client.get(f"/api/sites/{temp_site}/runs")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Legacy-route zero-regression: mirrors a couple of the exact assertions
# from tests/test_api_live.py (the fast ones -- /api/resolve's own ~230s
# coverage is not duplicated here) to prove Phase C's refactor changed
# nothing observable about the pre-existing khavda routes.
# ---------------------------------------------------------------------------


def test_legacy_solve_live_unchanged():
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


def test_legacy_solve_live_rejects_malformed_body_with_422():
    resp = client.post("/api/solve_live", json={"lat": "not-a-number", "lon": 69.7317, "soc_pct": 60})
    assert resp.status_code == 422


def test_legacy_runs_s2_mpc_matches_precomputed_file_exactly():
    precomputed_path = Path(api_main.RUNS_DIR) / "S2_mpc.json"
    assert precomputed_path.exists()
    resp = client.get("/api/runs/S2/mpc")
    assert resp.status_code == 200

    on_disk = RunResult.model_validate_json(precomputed_path.read_text(encoding="utf-8"))
    assert resp.json() == json.loads(on_disk.model_dump_json())
