"""
DIYA tests/test_pipeline_worker.py — Phase E: core/pipeline_worker.py.

Every test here monkeypatches core.pipeline_worker's build_site_scenarios to
a fast (~21 day) window, so none of this takes anywhere near the ~minute a
real 365-day build+precompute would -- see tests/test_site_pipeline.py's
own module docstring for the same fast-mode rationale.

Each test gets its OWN PipelineWorker() via the `worker` fixture below
(never api.main's real singleton), stopped again at teardown -- without an
explicit stop(), each test's daemon thread would otherwise keep running
for the rest of the session, piling up alongside every other test's worker
and the app's own real singleton and competing with them for CPU (this
was the root cause of a real, observed full-suite-only flake: these tests
passed reliably in isolation but occasionally timed out when run alongside
the rest of the suite, which also creates real sites via the API).

POLL_TIMEOUT_S is deliberately generous (not just "long enough in
isolation") for the same reason: the real api.main.PIPELINE_WORKER
singleton is shared by every other test file that creates a site via the
API, and its queue may still be working through an earlier test's backlog
while these tests run, competing for the same CPU.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest

import core.pipeline_worker as pipeline_worker
from core.pipeline_worker import PipelineWorker
from core.types import load_site_config

REPO_ROOT = Path(__file__).resolve().parents[1]
KHAVDA_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"

FAST_WEATHER_YEAR_END = date(2025, 1, 21)
FAST_WEATHER_WINDOW_DAYS = 21

POLL_TIMEOUT_S = 240
POLL_INTERVAL_S = 0.5


@pytest.fixture
def site():
    return load_site_config(KHAVDA_CONFIG_PATH)


@pytest.fixture
def worker():
    w = PipelineWorker()
    yield w
    w.stop()


@pytest.fixture(autouse=True)
def fast_build(monkeypatch):
    """Every test in this file gets build_site_scenarios's real
    implementation, just pinned to the fast ~21-day window instead of the
    365-day production default -- worker mechanics (queueing, status
    transitions, crash recovery) are what's under test here, not the
    pipeline's own computation (that's tests/test_site_pipeline.py's job)."""
    import core.site_pipeline as site_pipeline

    real_build = site_pipeline.build_site_scenarios

    def _fast(*args, **kwargs):
        kwargs.setdefault("weather_year_end", FAST_WEATHER_YEAR_END)
        kwargs.setdefault("weather_window_days", FAST_WEATHER_WINDOW_DAYS)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(pipeline_worker, "build_site_scenarios", _fast)


def _poll_until_done(worker: PipelineWorker, site_id: str, timeout_s: float = POLL_TIMEOUT_S):
    deadline = time.monotonic() + timeout_s
    seen_stages: list[str] = []
    while time.monotonic() < deadline:
        job = worker.get_status(site_id)
        assert job is not None
        if job.stage not in seen_stages:
            seen_stages.append(job.stage)
        if job.status in ("complete", "failed"):
            return job, seen_stages
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"job for {site_id!r} did not finish within {timeout_s}s (last seen: {seen_stages})")


def test_enqueue_and_poll_progresses_through_multiple_stages(tmp_path, site, worker):
    job = worker.enqueue("test_site", site, tmp_path / "scenarios", tmp_path / "runs", is_seed=False)
    assert job.status == "queued"

    final_job, seen_stages = _poll_until_done(worker, "test_site")

    assert final_job.status == "complete"
    assert final_job.started_at is not None
    assert final_job.finished_at is not None
    # More than just queued(None) -> complete("done") with nothing between --
    # real intermediate stages (weather/scenario build, then per-policy
    # precompute) must have been observed along the way.
    distinct_non_terminal = {s for s in seen_stages if s not in (None, "done")}
    assert len(distinct_non_terminal) >= 3, f"expected several distinct stages, saw: {seen_stages}"


def test_enqueue_same_site_twice_while_running_returns_same_job(tmp_path, site, worker):
    job1 = worker.enqueue("dup_site", site, tmp_path / "scenarios", tmp_path / "runs", is_seed=False)
    job2 = worker.enqueue("dup_site", site, tmp_path / "scenarios", tmp_path / "runs", is_seed=False)

    assert job1 is job2

    _poll_until_done(worker, "dup_site")


def test_job_failure_marks_failed_and_worker_survives_for_next_job(tmp_path, site, monkeypatch, worker):
    def _raise(*args, **kwargs):
        raise RuntimeError("simulated pipeline failure")

    monkeypatch.setattr(pipeline_worker, "build_site_scenarios", _raise)

    worker.enqueue("failing_site", site, tmp_path / "scenarios_fail", tmp_path / "runs_fail", is_seed=False)
    failed_job, _ = _poll_until_done(worker, "failing_site")

    assert failed_job.status == "failed"
    assert failed_job.error
    assert "simulated pipeline failure" in failed_job.error

    # Re-patch to a real (fast-mode) implementation -- proves the worker
    # thread is still alive and able to process a genuinely new job right
    # now, not merely that get_status still returns something.
    import core.site_pipeline as real_site_pipeline

    def _fast(*args, **kwargs):
        kwargs.setdefault("weather_year_end", FAST_WEATHER_YEAR_END)
        kwargs.setdefault("weather_window_days", FAST_WEATHER_WINDOW_DAYS)
        return real_site_pipeline.build_site_scenarios(*args, **kwargs)

    monkeypatch.setattr(pipeline_worker, "build_site_scenarios", _fast)

    worker.enqueue("recovery_site", site, tmp_path / "scenarios_ok", tmp_path / "runs_ok", is_seed=False)
    recovered_job, _ = _poll_until_done(worker, "recovery_site")

    assert recovered_job.status == "complete"


def test_is_seed_site_never_enqueued_even_called_directly(tmp_path, site, worker):
    """Defensive: the API layer is also supposed to guard this (see
    api/main.py's create_site/rebuild_site), but the worker must refuse on
    its own too -- both layers should refuse."""
    with pytest.raises(ValueError, match="seed site"):
        worker.enqueue("khavda", site, tmp_path / "scenarios", tmp_path / "runs", is_seed=True)

    assert worker.get_status("khavda") is None
