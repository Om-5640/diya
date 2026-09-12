"""
DIYA tests/test_api_pipeline.py — Phase E: pipeline_status/rebuild endpoints
and POST /api/sites' new background-job wiring, end to end via the real
app (api.main.app). tests/conftest.py's session-wide fixture keeps every
site created here on a fast (~21 day) pipeline instead of the real 365-day
production default, so polling to completion here takes well under a
minute, not real historical-build time.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import api.main as api_main

client = TestClient(api_main.app)

POLL_TIMEOUT_S = 240  # generous: the shared api.main.PIPELINE_WORKER singleton may still
# be working through other test files' queued jobs when these run (see
# tests/test_pipeline_worker.py's module docstring for the full explanation)
POLL_INTERVAL_S = 0.5


def _new_site_body(**overrides) -> dict:
    body = dict(
        display_name="Pipeline Test Site",
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


def _poll_pipeline_status_until_complete(site_id: str, timeout_s: float = POLL_TIMEOUT_S) -> dict:
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/sites/{site_id}/pipeline_status")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in ("complete", "failed"):
            return last
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"pipeline_status for {site_id!r} did not finish within {timeout_s}s (last: {last})")


def test_create_site_returns_fast_and_overview_never_shows_full_immediately(temp_site):
    start = time.perf_counter()
    resp = client.get(f"/api/sites/{temp_site}/overview")
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    assert elapsed < 5.0, f"overview took {elapsed:.2f}s -- POST /api/sites must not have blocked on the pipeline"
    data = resp.json()
    assert data["status"] in ("live_only", "building")
    assert data["status"] != "full"


def test_pipeline_status_progresses_to_complete_then_overview_and_scenarios_work(temp_site):
    final_status = _poll_pipeline_status_until_complete(temp_site)
    assert final_status["status"] == "complete"
    assert final_status["stage"] == "done"

    overview_resp = client.get(f"/api/sites/{temp_site}/overview")
    assert overview_resp.status_code == 200
    assert overview_resp.json()["status"] == "full"
    assert overview_resp.json()["has_precomputed_runs"] is True

    scenarios_resp = client.get(f"/api/sites/{temp_site}/scenarios")
    assert scenarios_resp.status_code == 200
    scenarios = scenarios_resp.json()
    assert len(scenarios) == 3
    assert {s["scenario_id"] for s in scenarios} == {"S1", "S2", "S3"}

    evidence_resp = client.get(f"/api/sites/{temp_site}/evidence")
    assert evidence_resp.status_code == 200
    evidence = evidence_resp.json()
    assert evidence["description"] is not None
    assert "khavda" not in evidence["description"].lower()


def test_pipeline_status_khavda_is_complete_no_job_ever_enqueued():
    resp = client.get("/api/sites/khavda/pipeline_status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "complete"
    # No job was ever enqueued for khavda -- get_status returns None for it,
    # so started_at/finished_at/error all stay null rather than being
    # fabricated from a job record that doesn't exist.
    assert data["started_at"] is None
    assert data["finished_at"] is None
    assert data["error"] is None


def test_pipeline_status_unknown_site_404():
    resp = client.get("/api/sites/does_not_exist_xyz/pipeline_status")
    assert resp.status_code == 404


def test_rebuild_khavda_rejected():
    resp = client.post("/api/sites/khavda/rebuild")
    assert resp.status_code == 400
    # khavda's data must be untouched by the rejected call
    status_resp = client.get("/api/sites/khavda/pipeline_status")
    assert status_resp.json()["status"] == "complete"


def test_rebuild_completed_site_re_enqueues_and_completes_again(temp_site):
    first = _poll_pipeline_status_until_complete(temp_site)
    assert first["status"] == "complete"

    rebuild_resp = client.post(f"/api/sites/{temp_site}/rebuild")
    assert rebuild_resp.status_code == 200
    rebuild_data = rebuild_resp.json()
    assert rebuild_data["status"] in ("queued", "running")

    second = _poll_pipeline_status_until_complete(temp_site)
    assert second["status"] == "complete"
