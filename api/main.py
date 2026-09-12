"""
DIYA api/main.py — FastAPI app.

PHASE 4: /api/runs and /api/runs/{scenario_id}/{policy} now serve the real
precomputed RunResult JSON from data/processed/runs/ (Phase 3's output, not
Phase 0's mocks). /api/solve_live and /api/resolve are real live/what-if
solves, built as thin wrappers around core/milp.py, core/simulator.py, and
core/policies.py -- their existing logic is not modified here.
response_model=RunResult on every run-returning route so pydantic validates
every response against the data contract in core/types.py.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from core.kpi import compute_kpi  # noqa: E402
from core.load import generate_load  # noqa: E402
from core.milp import solve_dispatch  # noqa: E402
from core.policies import _plan_window, run_mpc  # noqa: E402
from core.pv import pv_power_kw  # noqa: E402
from core.reasons import reason_for_planned_step  # noqa: E402
from core.types import (  # noqa: E402
    LiveSolveRequest,
    Provenance,
    ResolveRequest,
    RunResult,
    StepResult,
    config_hash,
    load_site_config,
)
from core.weather import fetch_forecast_live, load_cached_forecast  # noqa: E402
from core.wind import wind_power_kw  # noqa: E402

logger = logging.getLogger(__name__)

SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
SCENARIOS_YAML_PATH = REPO_ROOT / "config" / "scenarios.yaml"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
RUNS_DIR = PROCESSED_DIR / "runs"
RAW_DIR = REPO_ROOT / "data" / "raw"

CODE_VERSION = "phase4"
LIVE_LOAD_SEED = 20260101  # arbitrary but fixed: makes the live load pattern reproducible per hour-of-day/day-of-week

SITE = load_site_config(SITE_CONFIG_PATH)


def _load_scenario_names() -> dict[str, str]:
    raw = yaml.safe_load(SCENARIOS_YAML_PATH.read_text(encoding="utf-8"))["scenarios"]
    return {s["id"]: s["name"] for s in raw}


def _load_scenario_cfgs() -> dict[str, dict]:
    raw = yaml.safe_load(SCENARIOS_YAML_PATH.read_text(encoding="utf-8"))["scenarios"]
    return {s["id"]: s for s in raw}


SCENARIO_NAMES = _load_scenario_names()
SCENARIO_CFGS = _load_scenario_cfgs()

app = FastAPI(title="DIYA API", version="0.1.0-phase4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunListEntry(BaseModel):
    scenario_id: str
    policy: str
    name: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "phase": 4}


@app.get("/api/runs", response_model=list[RunListEntry])
def list_runs() -> list[RunListEntry]:
    entries = []
    for path in sorted(RUNS_DIR.glob("*.json")):
        scenario_id, policy = path.stem.split("_", 1)
        entries.append(RunListEntry(scenario_id=scenario_id, policy=policy, name=SCENARIO_NAMES.get(scenario_id, scenario_id)))
    return entries


@app.get("/api/runs/{scenario_id}/{policy}", response_model=RunResult)
def get_run(scenario_id: str, policy: str) -> RunResult:
    path = RUNS_DIR / f"{scenario_id}_{policy}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no precomputed run for {scenario_id}/{policy}")
    return RunResult.model_validate_json(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# /api/solve_live
# ---------------------------------------------------------------------------


def _current_hour_index(hours: int = 72) -> pd.DatetimeIndex:
    now = pd.Timestamp.now(tz="Asia/Kolkata").floor("h")
    return pd.date_range(now, periods=hours, freq="h", tz="Asia/Kolkata")


def _live_pv_wind_forecast(lat: float, lon: float) -> tuple[list[float], list[float], str]:
    """Returns (pv_fc_kw, wind_fc_kw, forecast_method), trying live fetch,
    then the last successful cache, then a scenario-replay fallback --
    never raises.
    """
    weather_df = None
    forecast_method = None

    try:
        weather_df = fetch_forecast_live(lat, lon, cache_dir=RAW_DIR)
        forecast_method = "open-meteo-forecast-live"
    except Exception:
        logger.warning("live forecast fetch failed, trying cache", exc_info=True)

    if weather_df is None:
        try:
            weather_df = load_cached_forecast(cache_dir=RAW_DIR)
            forecast_method = "cached-fallback"
        except Exception:
            logger.warning("no live forecast cache available, falling back to scenario replay", exc_info=True)

    if weather_df is not None:
        # NOTE: lat/lon drive solar position (pv_power_kw's own lat/lon args);
        # tilt/azimuth/capacity always come from site_khavda.yaml's PV spec --
        # a known simplification, since a real deployment elsewhere would have
        # its own panel orientation.
        pv_fc = pv_power_kw(weather_df, SITE.pv, lat, lon, SITE.elevation_m, SITE.timezone).tolist()
        wind_fc = wind_power_kw(weather_df, SITE.wind).tolist()
        return pv_fc, wind_fc, forecast_method

    s2_path = PROCESSED_DIR / "S2.parquet"
    s2_df = pd.read_parquet(s2_path).iloc[:72]
    return s2_df["pv_fc_kw"].tolist(), s2_df["wind_fc_kw"].tolist(), "scenario-replay-fallback"


@app.post("/api/solve_live", response_model=RunResult)
def solve_live(req: LiveSolveRequest) -> RunResult:
    idx = _current_hour_index(72)
    pv_fc, wind_fc, forecast_method = _live_pv_wind_forecast(req.lat, req.lon)

    # SIMPLIFICATION: load is treated as perfectly known "right now", same as
    # elsewhere in this MVP -- only weather/generation is genuinely uncertain.
    load_df = generate_load(idx, seed=LIVE_LOAD_SEED, load_multiplier=1.0)

    soc_init_kwh = req.soc_pct / 100.0 * SITE.battery.capacity_kwh

    # ONE solve over the full 72h horizon -- not a rolling per-hour loop like
    # run_mpc's inner loop, since there is no "realized" future to reconcile
    # against yet. Steps are read directly from the plan's own arrays, the
    # same pattern run_perfect_foresight uses for exactly that reason.
    plan = solve_dispatch(
        pv_forecast_kw=pv_fc,
        wind_forecast_kw=wind_fc,
        load_critical_kw=load_df["critical_kw"].tolist(),
        load_essential_kw=load_df["essential_kw"].tolist(),
        load_deferrable_kw=load_df["deferrable_kw"].tolist(),
        soc_init_kwh=soc_init_kwh,
        dg_on_prev=False,
        battery=SITE.battery,
        diesel=SITE.diesel,
        economics=SITE.economics,
        reserve_hours=SITE.reserve_hours,
        k_uncertainty=SITE.k_uncertainty,
        time_limit_s=3.0,
    )

    pv_fc_l, wind_fc_l = pv_fc, wind_fc
    critical_l = load_df["critical_kw"].tolist()
    essential_l = load_df["essential_kw"].tolist()
    deferrable_l = load_df["deferrable_kw"].tolist()

    steps: list[StepResult] = []
    for t in range(len(idx)):
        soc_prev_kwh = soc_init_kwh if t == 0 else plan.soc_kwh[t - 1]
        soc_pct_at_commit = 100.0 * soc_prev_kwh / SITE.battery.capacity_kwh

        code, text = reason_for_planned_step(
            _plan_window(plan, t),
            pv_fc_window=pv_fc_l[t:],
            wind_fc_window=wind_fc_l[t:],
            load_essential_fc_window=essential_l[t:],
            load_critical_fc_window=critical_l[t:],
            soc_pct_at_commit=soc_pct_at_commit,
            reserve_hours=SITE.reserve_hours,
            k_uncertainty=SITE.k_uncertainty,
            diesel=SITE.diesel,
            battery=SITE.battery,
        )

        steps.append(
            StepResult(
                t=idx[t].isoformat(),
                pv_kw=plan.pv_use_kw[t],
                wind_kw=plan.wind_use_kw[t],
                dg_kw=plan.dg_kw[t],
                dg_on=plan.dg_on[t],
                batt_kw=plan.p_discharge_kw[t] - plan.p_charge_kw[t],
                soc_kwh=plan.soc_kwh[t],
                soc_pct=100.0 * plan.soc_kwh[t] / SITE.battery.capacity_kwh,
                load_critical_kw=critical_l[t],
                load_essential_kw=essential_l[t],
                load_deferrable_kw=deferrable_l[t],
                unserved_critical_kwh=plan.unserved_critical_kwh[t],
                unserved_essential_kwh=plan.unserved_essential_kwh[t],
                unserved_deferrable_kwh=plan.unserved_deferrable_kwh[t],
                curtailed_kwh=plan.curtailed_kwh[t],
                fuel_l=plan.fuel_l[t],
                reason_code=code,
                reason_text=text,
            )
        )

    kpi = compute_kpi(
        steps,
        diesel=SITE.diesel,
        economics=SITE.economics,
        battery_capacity_kwh=SITE.battery.capacity_kwh,
        solve_ms_list=[plan.solve_ms],
    )

    provenance = Provenance(
        weather_source=forecast_method,
        forecast_method=forecast_method,
        load_method="synthetic-seeded-v1",
        diesel_price_source=SITE.economics.diesel_price_source,
        config_hash=config_hash(SITE),
        generated_at=datetime.now(timezone.utc).isoformat(),
        code_version=CODE_VERSION,
    )

    return RunResult(
        scenario_id="LIVE",
        policy="mpc",
        site_id=SITE.site_id,
        site_name=SITE.name,
        steps=steps,
        kpi=kpi,
        provenance=provenance,
        notes=f"Live 72h forecast-driven plan (forecast_method={forecast_method}).",
    )


# ---------------------------------------------------------------------------
# /api/resolve
#
# MEASURED TIMING (updated in DIYA v2 Phase B -- still ~60-65s, NOT fixed by
# that phase's solver swap, and that's a deliberate, empirically-justified
# outcome rather than an oversight): run_mpc's rolling window is 24 hours at
# a time, called 168 times per /api/resolve request. core/milp.py's solver
# routing (HIGHS_MIN_HORIZON_HOURS) sends horizons this small to
# PULP_CBC_CMD, not HiGHS, because CBC was measured to be FASTER than HiGHS
# for this many-small-solves problem shape (HiGHS wins decisively on ONE
# large solve -- see run_perfect_foresight / scripts/precompute_runs.py,
# ~7x faster there -- but loses on 168 small ones; see core/milp.py's
# benchmark comment for the actual numbers). So this endpoint's ~60-65s is
# still the real, accepted response time: Phase B's genuine win landed on
# the precompute pipeline, not here. A real fix for /api/resolve would need
# to stop rebuilding/resolving 168 independent MILPs from scratch (e.g. a
# persistent warm-started solver across the rolling loop) -- a formulation-
# adjacent change explicitly out of scope for a solver-backend swap.
# ---------------------------------------------------------------------------


@app.post("/api/resolve", response_model=RunResult)
def resolve(req: ResolveRequest) -> RunResult:
    if req.scenario_id not in SCENARIO_CFGS:
        raise HTTPException(status_code=404, detail=f"unknown scenario_id: {req.scenario_id}")

    parquet_path = PROCESSED_DIR / f"{req.scenario_id}.parquet"
    if not parquet_path.exists():
        raise HTTPException(status_code=404, detail=f"no scenario data for {req.scenario_id}")
    df = pd.read_parquet(parquet_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # weights.cost scales the fuel-price term (on top of the request's own
    # diesel_price_inr_per_l), weights.co2 scales the CO2 price, and
    # weights.reliability scales all three VOLL terms together -- a true
    # 3-way lever over the objective's cost/emissions/reliability tradeoffs.
    effective_diesel_price = req.diesel_price_inr_per_l * req.weights.cost
    adjusted_economics = SITE.economics.model_copy(
        update={
            "diesel_price_inr_per_l": effective_diesel_price,
            "co2_price_inr_per_kg": SITE.economics.co2_price_inr_per_kg * req.weights.co2,
            "voll_critical_inr_per_kwh": SITE.economics.voll_critical_inr_per_kwh * req.weights.reliability,
            "voll_essential_inr_per_kwh": SITE.economics.voll_essential_inr_per_kwh * req.weights.reliability,
            "voll_deferrable_inr_per_kwh": SITE.economics.voll_deferrable_inr_per_kwh * req.weights.reliability,
        }
    )
    adjusted_site = SITE.model_copy(update={"economics": adjusted_economics, "k_uncertainty": req.k_uncertainty})

    steps, solve_ms_list = run_mpc(df, adjusted_site, time_limit_s=1.5)

    kpi = compute_kpi(
        steps,
        diesel=adjusted_site.diesel,
        economics=adjusted_economics,
        battery_capacity_kwh=adjusted_site.battery.capacity_kwh,
        solve_ms_list=solve_ms_list,
    )

    provenance = Provenance(
        weather_source="open-meteo-archive",
        forecast_method="ar1-lead-scaled-noise-v1",
        load_method="synthetic-seeded-v1",
        diesel_price_source="user-override (weights applied via /api/resolve)",
        config_hash=config_hash(SITE),
        generated_at=datetime.now(timezone.utc).isoformat(),
        code_version=CODE_VERSION,
    )

    return RunResult(
        scenario_id=req.scenario_id,
        policy="mpc",
        site_id=SITE.site_id,
        site_name=SITE.name,
        steps=steps,
        kpi=kpi,
        provenance=provenance,
        notes="Re-solved via /api/resolve with user-adjusted weights/k_uncertainty/diesel price.",
    )
