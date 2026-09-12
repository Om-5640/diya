"""
DIYA core/pipeline_worker.py — single-threaded background job queue for
core/site_pipeline.py's build+precompute pipeline (DIYA v2 Phase E).

One daemon worker thread processes jobs strictly one at a time: Render's
free tier has limited shared CPU, and the mpc policy alone is ~168
sequential MILP solves per scenario, so running multiple sites' pipelines
concurrently risks starving or crashing the instance. Other sites' jobs
wait in a queue.Queue rather than each getting their own thread.

Job state is held ONLY in memory (dict[site_id, PipelineJob]) -- NOT
persisted to disk. If the process restarts mid-job, that job's record is
gone; this is an intentional, documented tradeoff (see DEPLOYMENT.md's
no-persistent-disk note), not an oversight. A site whose job record was
lost this way but whose parquet/run files already made it to disk before
the restart is still correctly detected as "complete" via the
has_precomputed_runs fallback that GET /api/sites/{id}/overview and
/pipeline_status both use.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from core.site_pipeline import build_site_scenarios, precompute_site_runs
from core.types import SiteConfig

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "complete", "failed"]


class PipelineJob(BaseModel):
    site_id: str
    status: JobStatus
    stage: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


@dataclass
class _QueueItem:
    site_id: str
    site: SiteConfig
    scenarios_dir: Path
    runs_dir: Path


_STOP = object()  # sentinel: tells _run_forever to exit cleanly


class PipelineWorker:
    """One instance should exist per process (a module-level singleton in
    api/main.py, NOT one per request) -- constructing more than one starts
    more than one background thread, defeating the "process jobs strictly
    one at a time" guarantee this class exists to provide.

    Production's singleton lives for the whole process and is never
    stopped. stop() exists for tests that construct their own short-lived
    PipelineWorker instances: without it, each test's daemon thread would
    keep running (idle, but still a live thread) for the rest of the test
    session, needlessly piling up alongside every other test's worker and
    the app's own real singleton.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[_QueueItem | object] = queue.Queue()
        self._jobs: dict[str, PipelineJob] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run_forever, name="diya-pipeline-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the worker thread to exit once it's idle (after finishing
        any job already in progress or queued ahead of the stop signal).
        Does not block waiting for the thread to actually exit."""
        self._queue.put(_STOP)

    def enqueue(self, site_id: str, site: SiteConfig, scenarios_dir: Path, runs_dir: Path, is_seed: bool = False) -> PipelineJob:
        """Enqueue a build+precompute job for site_id, or return the
        existing job if one is already queued or running for it (never a
        duplicate). A finished job (complete or failed) is replaced by a
        fresh one -- this is what POST /api/sites/{id}/rebuild relies on.

        is_seed=True (khavda) is refused with ValueError rather than a
        silent no-op: seed sites already have their real, hand-verified
        data, and a job overwriting it would be a bug, not a normal path,
        so a caller that gets this wrong finds out immediately. The API
        layer (api/main.py) is expected to guard this too before ever
        calling enqueue -- this is defense in depth, not the only guard.
        """
        if is_seed:
            raise ValueError(f"refusing to enqueue a pipeline job for seed site {site_id!r}")

        with self._lock:
            existing = self._jobs.get(site_id)
            if existing is not None and existing.status in ("queued", "running"):
                return existing
            job = PipelineJob(site_id=site_id, status="queued")
            self._jobs[site_id] = job

        self._queue.put(_QueueItem(site_id=site_id, site=site, scenarios_dir=scenarios_dir, runs_dir=runs_dir))
        return job

    def get_status(self, site_id: str) -> PipelineJob | None:
        with self._lock:
            return self._jobs.get(site_id)

    def _set_stage(self, site_id: str, stage: str) -> None:
        with self._lock:
            job = self._jobs.get(site_id)
            if job is not None:
                job.stage = stage

    def _run_forever(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                self._queue.task_done()
                return
            try:
                self._process(item)
            except Exception:
                # Last-resort safety net: _process already catches and
                # records failures from the pipeline calls themselves, so
                # reaching this means a bug in _process's own bookkeeping,
                # not a pipeline failure. Either way, the worker must keep
                # processing the queue for subsequent sites -- log and
                # continue, never let this thread die.
                logger.exception(
                    "core.pipeline_worker: unexpected error processing job for site_id=%s -- worker continues",
                    item.site_id,
                )
            finally:
                self._queue.task_done()

    def _process(self, item: _QueueItem) -> None:
        with self._lock:
            job = self._jobs[item.site_id]
            job.status = "running"
            job.started_at = datetime.now(timezone.utc).isoformat()

        def progress_cb(stage: str, message: str) -> None:
            logger.info("core.pipeline_worker[%s]: %s: %s", item.site_id, stage, message)
            self._set_stage(item.site_id, stage)

        try:
            build_site_scenarios(item.site_id, item.site, item.scenarios_dir, progress_cb=progress_cb)
            precompute_site_runs(item.site_id, item.site, item.scenarios_dir, item.runs_dir, progress_cb=progress_cb)
        except Exception as e:
            logger.exception("core.pipeline_worker: pipeline failed for site_id=%s", item.site_id)
            with self._lock:
                job = self._jobs[item.site_id]
                job.status = "failed"
                job.error = str(e)
                job.finished_at = datetime.now(timezone.utc).isoformat()
            return

        with self._lock:
            job = self._jobs[item.site_id]
            job.status = "complete"
            job.stage = "done"
            job.finished_at = datetime.now(timezone.utc).isoformat()
