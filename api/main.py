"""
DIYA api/main.py — FastAPI app.

PHASE C (DIYA v2): multi-site store. The single hardcoded SITE global is
gone -- every route handler now resolves a SiteConfig via STORE (a
core.site_store.SiteStore) instead. Legacy routes (/api/runs, /api/solve_live,
/api/resolve, ...) are thin wrappers that resolve site_id="khavda" and are
byte-identical in behaviour to before this phase; new /api/sites/... routes
do the same work for any registered site. Khavda's existing files under
config/ and data/processed/ are untouched -- STORE's seed record just points
at them.

PHASE 4 (unchanged by Phase C): /api/runs and /api/runs/{scenario_id}/{policy}
serve the real precomputed RunResult JSON from data/processed/runs/.
/api/solve_live and /api/resolve are real live/what-if solves, built as thin
wrappers around core/milp.py, core/simulator.py, and core/policies.py.
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
from fastapi import FastAPI, HTTPException, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from core.kpi import compute_kpi  # noqa: E402
from core.load import generate_load  # noqa: E402
from core.milp import solve_dispatch  # noqa: E402
from core.policies import _plan_window, run_mpc  # noqa: E402
from core.pv import pv_power_kw  # noqa: E402
from core.reasons import reason_for_planned_step  # noqa: E402
from core.site_store import KHAVDA_SITE_ID, SeedSiteProtectedError, SiteNotFoundError, SiteStore  # noqa: E402
from core.types import (  # noqa: E402
    BatterySpec,
    DieselSpec,
    EconomicsSpec,
    LiveSolveRequest,
    NewSiteRequest,
    Provenance,
    PVSpec,
    ResolveRequest,
    RunResult,
    SiteConfig,
    SiteRecord,
    SiteSummary,
    StepResult,
    WindSpec,
    config_hash,
)
from core.weather import fetch_forecast_live, load_cached_forecast  # noqa: E402
from core.wind import wind_power_kw  # noqa: E402

logger = logging.getLogger(__name__)

SCENARIOS_YAML_PATH = REPO_ROOT / "config" / "scenarios.yaml"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
RUNS_DIR = PROCESSED_DIR / "runs"
RAW_DIR = REPO_ROOT / "data" / "raw"

CODE_VERSION = "phase4"
LIVE_LOAD_SEED = 20260101  # arbitrary but fixed: makes the live load pattern reproducible per hour-of-day/day-of-week

STORE = SiteStore()


def _load_scenario_names() -> dict[str, str]:
    raw = yaml.safe_load(SCENARIOS_YAML_PATH.read_text(encoding="utf-8"))["scenarios"]
    return {s["id"]: s["name"] for s in raw}


def _load_scenario_cfgs() -> dict[str, dict]:
    raw = yaml.safe_load(SCENARIOS_YAML_PATH.read_text(encoding="utf-8"))["scenarios"]
    return {s["id"]: s for s in raw}


SCENARIO_NAMES = _load_scenario_names()
SCENARIO_CFGS = _load_scenario_cfgs()

app = FastAPI(title="DIYA API", version="0.1.0-phase4")

# KNOWN, TEMPORARY CHOICE (DIYA v2 Phase C.5): allow_origins=["*"] is wide
# open so an external frontend build (Lovable) can point at this API from
# whatever dynamic preview domain it gets assigned, without us having to
# predict/whitelist it in advance. Open CORS plus NO rate limiting is a
# bigger exposure than open CORS alone -- Phase G (rate limiting) is what
# actually closes this gap, not this phase. Revisit allow_origins once
# Phase G lands (e.g. pin to the real deployed frontend origin(s)).
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


class ScenarioSummary(BaseModel):
    scenario_id: str
    name: str


class HealthResponse(BaseModel):
    ok: bool
    phase: int


class ServiceInfo(BaseModel):
    service: str
    docs: str
    openapi: str
    health: str


class SiteUpdateRequest(BaseModel):
    """Partial update body for PATCH /api/sites/{site_id}. Every field is
    optional; only fields actually present in the request are applied
    (pydantic's exclude_unset, not "set to None") -- this preserves the
    original dict-based partial-update semantics of SiteStore.update while
    giving the field a real, documented type instead of an opaque object.
    On a seed site (khavda), only economics/horizon_hours/reserve_hours/
    k_uncertainty may be present; any other field raises 400.
    """

    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    elevation_m: float | None = None
    timezone: str | None = None
    pv: PVSpec | None = None
    wind: WindSpec | None = None
    battery: BatterySpec | None = None
    diesel: DieselSpec | None = None
    economics: EconomicsSpec | None = None
    horizon_hours: int | None = None
    reserve_hours: int | None = None
    k_uncertainty: float | None = None


def _get_record_or_404(site_id: str) -> SiteRecord:
    try:
        return STORE.get(site_id)
    except SiteNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown site_id: {site_id}")


@app.get("/", response_model=ServiceInfo)
def root() -> ServiceInfo:
    """Service pointer. Not part of the dispatch API itself -- just tells a
    caller landing on the bare host where to find the interactive docs, the
    raw OpenAPI schema, and the health check."""
    return ServiceInfo(service="DIYA API", docs="/docs", openapi="/openapi.json", health="/api/health")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness check. Always returns 200 with ok=true if the process is up
    and able to handle requests; no error cases."""
    return HealthResponse(ok=True, phase=4)


# ---------------------------------------------------------------------------
# /api/runs (shared internals, parameterized by runs_dir/scenario_names
# instead of the old module-level RUNS_DIR/SCENARIO_NAMES globals)
# ---------------------------------------------------------------------------


def _list_runs(runs_dir: Path, scenario_names: dict[str, str]) -> list[RunListEntry]:
    entries = []
    for path in sorted(runs_dir.glob("*.json")):
        scenario_id, policy = path.stem.split("_", 1)
        entries.append(RunListEntry(scenario_id=scenario_id, policy=policy, name=scenario_names.get(scenario_id, scenario_id)))
    return entries


def _get_run(runs_dir: Path, scenario_id: str, policy: str) -> RunResult:
    path = runs_dir / f"{scenario_id}_{policy}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no precomputed run for {scenario_id}/{policy}")
    return RunResult.model_validate_json(path.read_text(encoding="utf-8"))


@app.get("/api/runs", response_model=list[RunListEntry])
def list_runs() -> list[RunListEntry]:
    """List every precomputed dispatch run for Khavda (legacy, khavda-only).

    Returns one entry per (scenario_id, policy) pair found under
    data/processed/runs/. No error cases -- an empty list if none exist.
    """
    return _list_runs(RUNS_DIR, SCENARIO_NAMES)


@app.get("/api/runs/{scenario_id}/{policy}", response_model=RunResult)
def get_run(scenario_id: str, policy: str) -> RunResult:
    """Fetch one precomputed dispatch run for Khavda (legacy, khavda-only).

    Example: GET /api/runs/S2/mpc

    Errors: 404 if no precomputed run exists for that scenario_id/policy pair.
    """
    return _get_run(RUNS_DIR, scenario_id, policy)


@app.get("/api/sites/{site_id}/runs", response_model=list[RunListEntry])
def list_site_runs(site_id: str) -> list[RunListEntry]:
    """List every precomputed dispatch run for any registered site.

    Same shape as legacy GET /api/runs, generalized to site_id. A site with
    no precomputed runs yet (e.g. one just created via POST /api/sites)
    returns an empty list, not an error.

    Errors: 404 if site_id is not registered.
    """
    record = _get_record_or_404(site_id)
    runs_dir = STORE.resolve_path(record.runs_dir)
    names = SCENARIO_NAMES if site_id == KHAVDA_SITE_ID else {}
    return _list_runs(runs_dir, names)


@app.get("/api/sites/{site_id}/runs/{scenario_id}/{policy}", response_model=RunResult)
def get_site_run(site_id: str, scenario_id: str, policy: str) -> RunResult:
    """Fetch one precomputed dispatch run for any registered site.

    Example: GET /api/sites/khavda/runs/S2/mpc

    Errors: 404 if site_id is not registered, or if no precomputed run
    exists for that scenario_id/policy pair on this site.
    """
    record = _get_record_or_404(site_id)
    runs_dir = STORE.resolve_path(record.runs_dir)
    return _get_run(runs_dir, scenario_id, policy)


@app.get("/api/sites/{site_id}/scenarios", response_model=list[ScenarioSummary])
def list_site_scenarios(site_id: str) -> list[ScenarioSummary]:
    """List the scenarios available for a site.

    For khavda, returns the human-named S1/S2/S3 scenarios defined in
    config/scenarios.yaml. For any other site, returns one entry per
    scenario parquet file found under that site's own scenarios directory
    (id and name are the same string, since new sites have no separate
    human-naming step yet) -- an empty list if none have been uploaded.

    Errors: 404 if site_id is not registered.
    """
    _get_record_or_404(site_id)
    if site_id == KHAVDA_SITE_ID:
        return [ScenarioSummary(scenario_id=sid, name=name) for sid, name in SCENARIO_NAMES.items()]
    scenarios_dir = STORE.scenarios_dir_path(site_id)
    ids = sorted(p.stem for p in scenarios_dir.glob("*.parquet")) if scenarios_dir.exists() else []
    return [ScenarioSummary(scenario_id=sid, name=sid) for sid in ids]


# ---------------------------------------------------------------------------
# /api/sites — site management
# ---------------------------------------------------------------------------


def _to_summary(record: SiteRecord) -> SiteSummary:
    return SiteSummary(
        site_id=record.site_id,
        display_name=record.display_name,
        lat=record.lat,
        lon=record.lon,
        created_at=record.created_at,
        is_seed=record.is_seed,
    )


@app.get("/api/sites", response_model=list[SiteSummary])
def list_sites() -> list[SiteSummary]:
    """List every registered site (always includes the khavda seed site).

    No error cases.
    """
    return [_to_summary(r) for r in STORE.list()]


@app.post("/api/sites", response_model=SiteSummary, status_code=201)
def create_site(req: NewSiteRequest) -> SiteSummary:
    """Register a new site, deriving a full SiteConfig from minimal input.

    Example request body:
        {"display_name": "Example Site", "lat": -33.9, "lon": 18.4,
         "pv_capacity_kwp": 10.0, "battery_capacity_kwh": 30.0}

    PV tilt/azimuth are derived from lat (tilt = abs(lat), array facing the
    equator); wind is omitted (rated_kw=0); battery is sized at a 3-hour
    C-rate; diesel reuses Khavda's combustion-property constants at the
    given (or default) rated_kw; VOLL values are ratios of
    outage_cost_inr_per_kwh. See core/site_store.py for the exact defaults.

    No error cases beyond standard 422 body validation.
    """
    record = STORE.create(req)
    return _to_summary(record)


@app.get("/api/sites/{site_id}", response_model=SiteSummary)
def get_site(site_id: str) -> SiteSummary:
    """Fetch one registered site's summary.

    Errors: 404 if site_id is not registered.
    """
    return _to_summary(_get_record_or_404(site_id))


@app.patch("/api/sites/{site_id}", response_model=SiteSummary)
def patch_site(site_id: str, req: SiteUpdateRequest) -> SiteSummary:
    """Partially update a site's SiteConfig. Only fields present in the
    request body are changed; omitted fields are left as-is.

    Example request body: {"economics": {"diesel_price_inr_per_l": 100.0,
    "diesel_price_source": "manual override", "co2_price_inr_per_kg": 2.0,
    "voll_critical_inr_per_kwh": 500.0, "voll_essential_inr_per_kwh": 60.0,
    "voll_deferrable_inr_per_kwh": 12.0}}

    Errors: 404 if site_id is not registered. 400 if site_id is a seed site
    (khavda) and the request touches any field other than economics,
    horizon_hours, reserve_hours, or k_uncertainty -- Khavda's real hardware
    configuration must not be silently rewritten by an API call.
    """
    _get_record_or_404(site_id)
    updates = req.model_dump(exclude_unset=True)
    try:
        STORE.update(site_id, updates)
    except SeedSiteProtectedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_summary(STORE.get(site_id))


@app.delete("/api/sites/{site_id}", status_code=204)
def delete_site(site_id: str) -> Response:
    """Permanently delete a site: removes its registry entry and its own
    config/scenarios/runs directory (sites/{site_id}/).

    Errors: 404 if site_id is not registered. 400/403 if site_id is a seed
    site (khavda) -- seed sites can never be deleted through this API.
    """
    _get_record_or_404(site_id)
    try:
        STORE.delete(site_id)
    except SeedSiteProtectedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# /api/solve_live
# ---------------------------------------------------------------------------


def _current_hour_index(hours: int = 72) -> pd.DatetimeIndex:
    now = pd.Timestamp.now(tz="Asia/Kolkata").floor("h")
    return pd.date_range(now, periods=hours, freq="h", tz="Asia/Kolkata")


def _live_pv_wind_forecast(lat: float, lon: float, site: SiteConfig) -> tuple[list[float], list[float], str]:
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
        # tilt/azimuth/capacity always come from the site's own PV spec --
        # a known simplification, since a real deployment elsewhere would have
        # its own panel orientation.
        pv_fc = pv_power_kw(weather_df, site.pv, lat, lon, site.elevation_m, site.timezone).tolist()
        wind_fc = wind_power_kw(weather_df, site.wind).tolist()
        return pv_fc, wind_fc, forecast_method

    s2_path = PROCESSED_DIR / "S2.parquet"
    s2_df = pd.read_parquet(s2_path).iloc[:72]
    return s2_df["pv_fc_kw"].tolist(), s2_df["wind_fc_kw"].tolist(), "scenario-replay-fallback"


def _solve_live(req: LiveSolveRequest, site: SiteConfig) -> RunResult:
    idx = _current_hour_index(72)
    pv_fc, wind_fc, forecast_method = _live_pv_wind_forecast(req.lat, req.lon, site)

    # SIMPLIFICATION: load is treated as perfectly known "right now", same as
    # elsewhere in this MVP -- only weather/generation is genuinely uncertain.
    load_df = generate_load(idx, seed=LIVE_LOAD_SEED, load_multiplier=1.0)

    soc_init_kwh = req.soc_pct / 100.0 * site.battery.capacity_kwh

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
        battery=site.battery,
        diesel=site.diesel,
        economics=site.economics,
        reserve_hours=site.reserve_hours,
        k_uncertainty=site.k_uncertainty,
        time_limit_s=3.0,
    )

    pv_fc_l, wind_fc_l = pv_fc, wind_fc
    critical_l = load_df["critical_kw"].tolist()
    essential_l = load_df["essential_kw"].tolist()
    deferrable_l = load_df["deferrable_kw"].tolist()

    steps: list[StepResult] = []
    for t in range(len(idx)):
        soc_prev_kwh = soc_init_kwh if t == 0 else plan.soc_kwh[t - 1]
        soc_pct_at_commit = 100.0 * soc_prev_kwh / site.battery.capacity_kwh

        code, text = reason_for_planned_step(
            _plan_window(plan, t),
            pv_fc_window=pv_fc_l[t:],
            wind_fc_window=wind_fc_l[t:],
            load_essential_fc_window=essential_l[t:],
            load_critical_fc_window=critical_l[t:],
            soc_pct_at_commit=soc_pct_at_commit,
            reserve_hours=site.reserve_hours,
            k_uncertainty=site.k_uncertainty,
            diesel=site.diesel,
            battery=site.battery,
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
                soc_pct=100.0 * plan.soc_kwh[t] / site.battery.capacity_kwh,
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
        diesel=site.diesel,
        economics=site.economics,
        battery_capacity_kwh=site.battery.capacity_kwh,
        solve_ms_list=[plan.solve_ms],
    )

    provenance = Provenance(
        weather_source=forecast_method,
        forecast_method=forecast_method,
        load_method="synthetic-seeded-v1",
        diesel_price_source=site.economics.diesel_price_source,
        config_hash=config_hash(site),
        generated_at=datetime.now(timezone.utc).isoformat(),
        code_version=CODE_VERSION,
    )

    return RunResult(
        scenario_id="LIVE",
        policy="mpc",
        site_id=site.site_id,
        site_name=site.name,
        steps=steps,
        kpi=kpi,
        provenance=provenance,
        notes=f"Live 72h forecast-driven plan (forecast_method={forecast_method}).",
    )


@app.post("/api/solve_live", response_model=RunResult)
def solve_live(req: LiveSolveRequest) -> RunResult:
    """One-shot 72-hour forecast-driven dispatch plan for Khavda, using
    real live weather for (lat, lon) where possible (legacy, khavda-only).

    Example request body: {"lat": 23.8443, "lon": 69.7317, "soc_pct": 60.0}

    Weather falls back live-fetch -> last cached forecast -> scenario replay
    if the network is unavailable; provenance.forecast_method in the
    response says which was actually used. No error cases -- this endpoint
    never raises on a weather-fetch failure, only on malformed input (422).
    """
    site = STORE.load_config(KHAVDA_SITE_ID)
    return _solve_live(req, site)


@app.post("/api/sites/{site_id}/solve_live", response_model=RunResult)
def solve_live_for_site(site_id: str, req: LiveSolveRequest) -> RunResult:
    """Same as legacy POST /api/solve_live, generalized to any registered
    site's own hardware/economics configuration.

    Errors: 404 if site_id is not registered; otherwise same as legacy
    /api/solve_live (never raises on a weather-fetch failure).
    """
    _get_record_or_404(site_id)
    site = STORE.load_config(site_id)
    return _solve_live(req, site)


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


def _resolve(req: ResolveRequest, site: SiteConfig, parquet_path: Path) -> RunResult:
    if not parquet_path.exists():
        raise HTTPException(status_code=404, detail=f"no scenario data for {req.scenario_id}")
    df = pd.read_parquet(parquet_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # weights.cost scales the fuel-price term (on top of the request's own
    # diesel_price_inr_per_l), weights.co2 scales the CO2 price, and
    # weights.reliability scales all three VOLL terms together -- a true
    # 3-way lever over the objective's cost/emissions/reliability tradeoffs.
    effective_diesel_price = req.diesel_price_inr_per_l * req.weights.cost
    adjusted_economics = site.economics.model_copy(
        update={
            "diesel_price_inr_per_l": effective_diesel_price,
            "co2_price_inr_per_kg": site.economics.co2_price_inr_per_kg * req.weights.co2,
            "voll_critical_inr_per_kwh": site.economics.voll_critical_inr_per_kwh * req.weights.reliability,
            "voll_essential_inr_per_kwh": site.economics.voll_essential_inr_per_kwh * req.weights.reliability,
            "voll_deferrable_inr_per_kwh": site.economics.voll_deferrable_inr_per_kwh * req.weights.reliability,
        }
    )
    adjusted_site = site.model_copy(update={"economics": adjusted_economics, "k_uncertainty": req.k_uncertainty})

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
        config_hash=config_hash(site),
        generated_at=datetime.now(timezone.utc).isoformat(),
        code_version=CODE_VERSION,
    )

    return RunResult(
        scenario_id=req.scenario_id,
        policy="mpc",
        site_id=site.site_id,
        site_name=site.name,
        steps=steps,
        kpi=kpi,
        provenance=provenance,
        notes="Re-solved via /api/resolve with user-adjusted weights/k_uncertainty/diesel price.",
    )


@app.post("/api/resolve", response_model=RunResult)
def resolve(req: ResolveRequest) -> RunResult:
    """Re-solve a full Khavda scenario (S1/S2/S3) via rolling MPC with
    user-adjusted cost/CO2/reliability weights, k_uncertainty, and diesel
    price (legacy, khavda-only).

    Example request body: {"scenario_id": "S1", "weights": {"cost": 1.0,
    "co2": 1.0, "reliability": 1.0}, "k_uncertainty": 1.0,
    "diesel_price_inr_per_l": 92.5}

    SLOW: ~60-65s (168 sequential 24h MILP solves) -- see the comment block
    above this route for why, and FREEZE_NOTES.md for the accepted timing.

    Errors: 404 if scenario_id is not one of the configured scenarios, or if
    that scenario has no parquet data on disk.
    """
    if req.scenario_id not in SCENARIO_CFGS:
        raise HTTPException(status_code=404, detail=f"unknown scenario_id: {req.scenario_id}")
    site = STORE.load_config(KHAVDA_SITE_ID)
    parquet_path = PROCESSED_DIR / f"{req.scenario_id}.parquet"
    return _resolve(req, site, parquet_path)


@app.post("/api/sites/{site_id}/resolve", response_model=RunResult)
def resolve_for_site(site_id: str, req: ResolveRequest) -> RunResult:
    """Same as legacy POST /api/resolve, generalized to any registered
    site's own scenario data (looked up as
    <that site's scenarios_dir>/{scenario_id}.parquet).

    Errors: 404 if site_id is not registered, or if that site has no
    parquet data for the given scenario_id (e.g. a newly created site with
    no scenarios uploaded yet).
    """
    _get_record_or_404(site_id)
    site = STORE.load_config(site_id)
    parquet_path = STORE.scenarios_dir_path(site_id) / f"{req.scenario_id}.parquet"
    return _resolve(req, site, parquet_path)
