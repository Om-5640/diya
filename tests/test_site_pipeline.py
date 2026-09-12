"""
DIYA tests/test_site_pipeline.py — Phase E: core/site_pipeline.py.

FAST MODE ONLY: every test here uses a short (~21 day) weather_window_days
override via build_site_scenarios's weather_window_days parameter, never a
full year -- too slow and too much live network dependency for CI-style
runs. (Real site creation always uses the 365-day default; the full-year
path is exercised only via the separate, manual Khavda byte-identical
verification, not re-run here.)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import core.site_pipeline as site_pipeline
from core.site_pipeline import build_site_scenarios, load_scenarios_yaml, precompute_site_runs
from core.types import RunResult, load_site_config

REPO_ROOT = Path(__file__).resolve().parents[1]
KHAVDA_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"

# Same window used across this file's fast-mode tests -- matches an
# already-cached data/raw/ file from prior development, so these tests hit
# no live network once that cache exists.
FAST_WEATHER_YEAR_END = date(2025, 1, 21)
FAST_WEATHER_WINDOW_DAYS = 21

EXPECTED_PARQUET_COLUMNS = [
    "timestamp", "ghi", "dni", "dhi", "temp_c", "wind_ms",
    "pv_actual_kw", "pv_fc_kw", "wind_actual_kw", "wind_fc_kw",
    "load_critical_kw", "load_essential_kw", "load_deferrable_kw",
]


@pytest.fixture
def site():
    return load_site_config(KHAVDA_CONFIG_PATH)


def _fast_build(site_id: str, site_cfg, scenarios_dir: Path, **overrides):
    kwargs = dict(weather_year_end=FAST_WEATHER_YEAR_END, weather_window_days=FAST_WEATHER_WINDOW_DAYS)
    kwargs.update(overrides)
    return build_site_scenarios(site_id, site_cfg, scenarios_dir, **kwargs)


# ---------------------------------------------------------------------------
# build_site_scenarios
# ---------------------------------------------------------------------------


def test_build_site_scenarios_writes_valid_parquets(tmp_path, site):
    scenarios_dir = tmp_path / "scenarios"
    result = _fast_build("test_site", site, scenarios_dir)

    assert set(result.scenario_windows.keys()) == {"S1", "S2", "S3"}
    assert result.weather_rows == FAST_WEATHER_WINDOW_DAYS * 24

    window_start = FAST_WEATHER_YEAR_END.replace(day=1)  # 2025-01-01, the fast window's start
    for scenario_id in ("S1", "S2", "S3"):
        path = scenarios_dir / f"{scenario_id}.parquet"
        assert path.exists()
        df = pd.read_parquet(path)
        assert list(df.columns) == EXPECTED_PARQUET_COLUMNS
        assert len(df) == 168  # a 7-day scenario window at 1h steps

        meta = result.scenario_windows[scenario_id]
        assert date.fromisoformat(meta.start_date) >= window_start
        assert date.fromisoformat(meta.end_date) <= FAST_WEATHER_YEAR_END


def test_build_site_scenarios_generates_generic_descriptions(tmp_path, site):
    scenarios_dir = tmp_path / "scenarios"
    _fast_build("test_site", site, scenarios_dir)

    cfgs = load_scenarios_yaml(scenarios_dir / "scenarios.yaml")
    assert set(cfgs.keys()) == {"S1", "S2", "S3"}
    for cfg in cfgs.values():
        assert "khavda" not in cfg["description"].lower()
        assert "monsoon" not in cfg["description"].lower()

    assert cfgs["S3"]["diesel_price_multiplier"] == pytest.approx(1.4)
    assert cfgs["S3"]["load_multiplier"] == pytest.approx(1.2)
    assert cfgs["S1"]["diesel_price_multiplier"] == pytest.approx(1.0)
    assert cfgs["S3"]["start_date"] == cfgs["S1"]["start_date"]
    assert cfgs["S3"]["end_date"] == cfgs["S1"]["end_date"]


def test_generate_scenarios_yaml_false_skips_writing(tmp_path, site):
    scenarios_dir = tmp_path / "scenarios"
    _fast_build("test_site", site, scenarios_dir, generate_scenarios_yaml=False)
    assert not (scenarios_dir / "scenarios.yaml").exists()
    assert (scenarios_dir / "S1.parquet").exists()  # parquets are written regardless


def test_build_site_scenarios_propagates_weather_fetch_error(tmp_path, site, monkeypatch):
    """The WORKER (core/pipeline_worker.py) is what catches exceptions and
    marks a job failed -- this function itself must propagate cleanly
    rather than silently returning partial/empty data."""

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated weather fetch failure")

    monkeypatch.setattr(site_pipeline, "fetch_weather", _raise)

    with pytest.raises(RuntimeError, match="simulated weather fetch failure"):
        _fast_build("test_site", site, tmp_path / "scenarios")


# ---------------------------------------------------------------------------
# precompute_site_runs
# ---------------------------------------------------------------------------


def test_precompute_site_runs_end_to_end(tmp_path, site):
    scenarios_dir = tmp_path / "scenarios"
    runs_dir = tmp_path / "runs"
    _fast_build("test_site", site, scenarios_dir)

    result = precompute_site_runs("test_site", site, scenarios_dir, runs_dir, mpc_time_limit_s=2.0)

    assert len(result.runs) == 12  # 3 scenarios x 4 policies
    json_files = sorted(runs_dir.glob("*.json"))
    assert len(json_files) == 12
    for path in json_files:
        run = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
        assert run.policy in {"diesel_only", "rule_based", "mpc", "perfect_foresight"}
        assert len(run.steps) == 168


def test_precompute_site_runs_works_with_fewer_than_three_scenarios(tmp_path, site):
    """Never hardcoded to "must be exactly S1/S2/S3" -- confirm it
    iterates whatever parquet files are actually present."""
    scenarios_dir = tmp_path / "scenarios"
    runs_dir = tmp_path / "runs"
    _fast_build("test_site", site, scenarios_dir)
    (scenarios_dir / "S3.parquet").unlink()

    result = precompute_site_runs("test_site", site, scenarios_dir, runs_dir, mpc_time_limit_s=2.0)

    scenario_ids = {scenario_id for scenario_id, _policy in result.runs}
    assert scenario_ids == {"S1", "S2"}
    assert len(result.runs) == 8


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


def test_progress_callback_sees_multiple_distinct_stages(tmp_path, site):
    scenarios_dir = tmp_path / "scenarios"
    runs_dir = tmp_path / "runs"
    stages: list[str] = []

    def cb(stage: str, message: str) -> None:
        stages.append(stage)

    _fast_build("test_site", site, scenarios_dir, progress_cb=cb)
    build_stages = set(stages)
    assert len(build_stages) >= 2  # e.g. fetching_weather, selecting_scenario_windows, ...

    stages.clear()
    precompute_site_runs("test_site", site, scenarios_dir, runs_dir, mpc_time_limit_s=2.0, progress_cb=cb)
    precompute_stages = set(stages)
    for policy in ("diesel_only", "rule_based", "mpc", "perfect_foresight"):
        assert any(s.startswith(f"precomputing_{policy}_") for s in precompute_stages), (
            f"no precompute stage seen for policy={policy}: {precompute_stages}"
        )


# ---------------------------------------------------------------------------
# BUGFIX-1: a site created with diesel omitted must show zero diesel
# activity across the ENTIRE real pipeline -- not just in an isolated MILP
# toy instance (tests/test_milp.py covers that), but through the real
# SiteStore.create() construction path and all 4 real policies.
# ---------------------------------------------------------------------------


def test_diesel_omitted_site_shows_zero_diesel_activity_across_full_pipeline(tmp_path):
    from core.site_store import SiteStore
    from core.types import NewSiteRequest

    store = SiteStore(registry_path=tmp_path / "registry.json", site_dir_base=tmp_path / "sites")
    req = NewSiteRequest(
        display_name="Diesel Omitted Test Site",
        lat=-33.9,
        lon=18.4,
        pv_capacity_kwp=15.0,
        battery_capacity_kwh=20.0,
        # diesel_rated_kw deliberately omitted, matching a form field left blank
    )
    record = store.create(req)
    site = store.load_config(record.site_id)
    assert site.diesel.rated_kw == 0.0

    scenarios_dir = store.scenarios_dir_path(record.site_id)
    runs_dir = store.runs_dir_path(record.site_id)
    _fast_build(record.site_id, site, scenarios_dir)
    result = precompute_site_runs(record.site_id, site, scenarios_dir, runs_dir, mpc_time_limit_s=2.0)

    assert len(result.runs) == 12  # 3 scenarios x 4 policies
    for (scenario_id, policy), run in result.runs.items():
        assert run.kpi.diesel_l == pytest.approx(0.0, abs=1e-9), f"{scenario_id}/{policy}: diesel_l != 0"
        assert run.kpi.dg_starts == 0, f"{scenario_id}/{policy}: dg_starts != 0 (phantom diesel start)"
        for step in run.steps:
            assert step.dg_kw == pytest.approx(0.0, abs=1e-9), f"{scenario_id}/{policy}: dg_kw != 0 at {step.t}"
            assert step.fuel_l == pytest.approx(0.0, abs=1e-9), f"{scenario_id}/{policy}: fuel_l != 0 at {step.t}"
