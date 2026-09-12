"""
DIYA tests/conftest.py — shared pytest fixtures.

autouse, session-scoped: pins core.pipeline_worker's build_site_scenarios to
a fast (~21 day) weather window for the ENTIRE test session (DIYA v2 Phase
E). POST /api/sites now enqueues a real background pipeline job for every
new site -- production default: a full 365-day historical build. Without
this fixture, every test anywhere in the suite that creates a throwaway
site via the real API (there are many, across every phase since Phase C)
would each silently queue a genuinely slow, real-network 365-day job onto
the single shared background worker (core/pipeline_worker.py is
deliberately single-threaded), backing it up for the rest of the test run.
This fixture makes every such job fast and harmless instead, without
touching any individual test file's own logic or assertions.

tests/test_site_pipeline.py calls build_site_scenarios directly with its
own explicit fast-mode kwargs and is unaffected by this fixture.
tests/test_pipeline_worker.py additionally monkeypatches per-test (on top
of this session-level patch) where it needs a failure/recovery sequence.
"""

from __future__ import annotations

from datetime import date

import pytest

import core.pipeline_worker as pipeline_worker
import core.site_pipeline as site_pipeline

_FAST_WEATHER_YEAR_END = date(2025, 1, 21)
_FAST_WEATHER_WINDOW_DAYS = 21


@pytest.fixture(autouse=True, scope="session")
def _fast_mode_pipeline_for_tests():
    real_build = site_pipeline.build_site_scenarios

    def _fast_build(*args, **kwargs):
        kwargs.setdefault("weather_year_end", _FAST_WEATHER_YEAR_END)
        kwargs.setdefault("weather_window_days", _FAST_WEATHER_WINDOW_DAYS)
        return real_build(*args, **kwargs)

    pipeline_worker.build_site_scenarios = _fast_build
    yield
    pipeline_worker.build_site_scenarios = real_build
