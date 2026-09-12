"""
DIYA core/site_pipeline.py — generalized data-build + precompute pipeline
(DIYA v2 Phase E).

Extracted from scripts/build_scenarios.py and scripts/precompute_runs.py so
ANY registered site (not just khavda) can get real historical scenarios and
precomputed policy runs, not just a live 72h solve. The two scripts are now
thin wrappers around build_site_scenarios/precompute_site_runs, calling them
with khavda's exact historical inputs so their output is unchanged.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW, fuel in L.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
import yaml

from core.forecast import make_forecast
from core.load import generate_load
from core.pv import pv_power_kw
from core.runner import run_scenario_policy
from core.types import RunResult, SiteConfig
from core.weather import fetch_weather
from core.wind import wind_power_kw

REPO_ROOT = Path(__file__).resolve().parents[1]

ProgressCallback = Callable[[str, str], None]

POLICIES = ["diesel_only", "rule_based", "mpc", "perfect_foresight"]

# S3's stress-test ratios: general "stress the diesel economics" choices,
# not khavda-specific facts -- the same ratios khavda has always used.
S3_DIESEL_PRICE_MULTIPLIER = 1.4
S3_LOAD_MULTIPLIER = 1.2
SCENARIO_LOAD_MULTIPLIER = {"S1": 1.0, "S2": 1.0, "S3": S3_LOAD_MULTIPLIER}
_SCENARIO_DIESEL_MULTIPLIER = {"S1": 1.0, "S2": 1.0, "S3": S3_DIESEL_PRICE_MULTIPLIER}

# Generic, site-agnostic scenario names/descriptions used for every new
# site's generated scenarios.yaml. Khavda keeps its own hand-written names/
# descriptions in config/scenarios.yaml, untouched by this module (see
# build_site_scenarios's generate_scenarios_yaml=False path).
_GENERIC_NAMES = {"S1": "Normal week", "S2": "Low-irradiance stretch", "S3": "Elevated cost scenario"}
_GENERIC_DESCRIPTIONS = {
    "S1": "Representative week based on this site's median solar output over the past year.",
    "S2": (
        "HERO SCENARIO. The site's worst two-day stretch of low irradiance in the past year, "
        "stressing battery and diesel dispatch to protect critical load."
    ),
    "S3": "Same weather as the normal week, with diesel priced 40% higher and load 20% higher -- an economic stress test.",
}


def _noop_progress(stage: str, message: str) -> None:
    pass


@dataclass
class ScenarioWindow:
    scenario_id: str
    name: str
    start_date: str
    end_date: str
    description: str
    diesel_price_multiplier: float
    load_multiplier: float
    k_uncertainty: float


@dataclass
class ScenariosBuildResult:
    scenario_windows: dict[str, ScenarioWindow]
    scenario_dataframes: dict[str, pd.DataFrame]
    weather_rows: int
    weekly_diagnostics: pd.DataFrame


@dataclass
class PrecomputeResult:
    runs: dict[tuple[str, str], RunResult]  # (scenario_id, policy) -> RunResult


# ---------------------------------------------------------------------------
# Shared scenarios.yaml schema (read AND write side) -- one function each,
# reused by khavda's config/scenarios.yaml and every generated
# {scenarios_dir}/scenarios.yaml, never duplicated.
# ---------------------------------------------------------------------------


def load_scenarios_yaml(path: Path) -> dict[str, dict]:
    """Parse a scenarios.yaml file into {scenario_id: cfg_dict}. Returns {}
    if the file doesn't exist (a new site with no completed pipeline run
    yet), so callers can treat "no scenarios.yaml" as an empty result
    rather than an error."""
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {s["id"]: s for s in raw.get("scenarios", [])}


def _write_scenarios_yaml(path: Path, scenario_meta: dict[str, ScenarioWindow]) -> None:
    doc = {
        "scenarios": [
            {
                "id": meta.scenario_id,
                "name": meta.name,
                "start_date": meta.start_date,
                "end_date": meta.end_date,
                "description": meta.description,
                "diesel_price_multiplier": meta.diesel_price_multiplier,
                "load_multiplier": meta.load_multiplier,
                "k_uncertainty": meta.k_uncertainty,
            }
            for meta in scenario_meta.values()
        ]
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Scenario window selection (verbatim from scripts/build_scenarios.py,
# generalized to take its inputs as parameters instead of module globals)
# ---------------------------------------------------------------------------


def _seed_for(scenario_id: str, kind: str) -> int:
    # Deliberately NOT site-id-scoped: this is khavda's exact, unchanged
    # formula from before Phase E. Changing it would change khavda's
    # synthetic noise for the same seed inputs, violating this phase's
    # hard byte-identical constraint. Every site reuses the same formula
    # per (scenario_id, kind) -- harmless, since each site's actual load/
    # forecast values still differ via its own SiteConfig and weather.
    digest = hashlib.sha256(f"{scenario_id}|{kind}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _weekly_diagnostics(pv_actual_kw: pd.Series, wind_actual_kw: pd.Series) -> pd.DataFrame:
    daily_pv = pv_actual_kw.resample("1D").sum()
    daily_wind = wind_actual_kw.resample("1D").sum()
    n_weeks = len(daily_pv) // 7

    rows = []
    for w in range(n_weeks):
        pv_week = daily_pv.iloc[w * 7 : (w + 1) * 7]
        wind_week = daily_wind.iloc[w * 7 : (w + 1) * 7]
        two_day_sums = [pv_week.iloc[i] + pv_week.iloc[i + 1] for i in range(len(pv_week) - 1)]
        rows.append(
            {
                "week": w,
                "start_date": pv_week.index[0].date().isoformat(),
                "end_date": pv_week.index[-1].date().isoformat(),
                "mean_daily_pv_kwh": pv_week.mean(),
                "mean_daily_wind_kwh": wind_week.mean(),
                "min_2day_pv_kwh": min(two_day_sums),
            }
        )
    return pd.DataFrame(rows)


def _select_scenario_weeks(diag: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    median_pv = diag["mean_daily_pv_kwh"].median()
    dist_to_median = (diag["mean_daily_pv_kwh"] - median_pv).abs()
    s1_row = diag.loc[dist_to_median.idxmin()]
    s2_row = diag.loc[diag["min_2day_pv_kwh"].idxmin()]
    return s1_row, s2_row


def _slice_week(df: pd.DataFrame | pd.Series, start_date: str, end_date: str):
    tz = df.index.tz
    start_ts = pd.Timestamp(start_date, tz=tz)
    end_ts = pd.Timestamp(end_date, tz=tz) + pd.Timedelta(hours=23)
    return df.loc[start_ts:end_ts]


def _build_scenario_parquet(
    scenario_id: str,
    start_date: str,
    end_date: str,
    weather_df: pd.DataFrame,
    pv_actual_kw: pd.Series,
    wind_actual_kw: pd.Series,
    scenarios_dir: Path,
) -> pd.DataFrame:
    weather_week = _slice_week(weather_df, start_date, end_date)
    pv_week = _slice_week(pv_actual_kw, start_date, end_date)
    wind_week = _slice_week(wind_actual_kw, start_date, end_date)

    load_multiplier = SCENARIO_LOAD_MULTIPLIER[scenario_id]
    load_df = generate_load(weather_week.index, seed=_seed_for(scenario_id, "load"), load_multiplier=load_multiplier)

    pv_fc = make_forecast(pv_week, seed=_seed_for(scenario_id, "pv_fc"), lead_hours=24)
    wind_fc = make_forecast(wind_week, seed=_seed_for(scenario_id, "wind_fc"), lead_hours=24)

    out = pd.DataFrame(
        {
            "timestamp": weather_week.index,
            "ghi": weather_week["ghi"].to_numpy(),
            "dni": weather_week["dni"].to_numpy(),
            "dhi": weather_week["dhi"].to_numpy(),
            "temp_c": weather_week["temp_c"].to_numpy(),
            "wind_ms": weather_week["wind_ms"].to_numpy(),
            "pv_actual_kw": pv_week.to_numpy(),
            "pv_fc_kw": pv_fc.to_numpy(),
            "wind_actual_kw": wind_week.to_numpy(),
            "wind_fc_kw": wind_fc.to_numpy(),
            "load_critical_kw": load_df["critical_kw"].to_numpy(),
            "load_essential_kw": load_df["essential_kw"].to_numpy(),
            "load_deferrable_kw": load_df["deferrable_kw"].to_numpy(),
        }
    )

    scenarios_dir.mkdir(parents=True, exist_ok=True)
    out_path = scenarios_dir / f"{scenario_id}.parquet"
    out.to_parquet(out_path, engine="pyarrow", index=False)
    return out


def build_site_scenarios(
    site_id: str,
    site: SiteConfig,
    scenarios_dir: Path,
    weather_year_end: date | None = None,
    weather_window_days: int = 365,
    generate_scenarios_yaml: bool = True,
    progress_cb: ProgressCallback = _noop_progress,
) -> ScenariosBuildResult:
    """Fetch/cache a rolling weather_window_days-day weather window ending
    at weather_year_end (defaults to yesterday, since Open-Meteo's archive
    API has a short latency tail), compute PV/wind actuals and synthetic
    load/forecast noise, auto-select S1 (median week), S2 (worst
    2-consecutive-day irradiance stretch), and S3 (same window as S1, with
    a 1.4x diesel price / 1.2x load stress multiplier -- the same ratios
    khavda uses, general "stress the diesel economics" choices rather than
    khavda-specific facts), and write {scenarios_dir}/{S1,S2,S3}.parquet.

    weather_window_days DEFAULTS TO 365 and real site creation must always
    use that default (the standing instruction: use the full 365-day
    window, same scope as khavda -- do not shorten it). The only caller
    that ever passes a smaller value is tests/test_site_pipeline.py's fast
    mode (~14-21 days), so the test suite doesn't need a full year of live
    weather + a full 12-scenario-run precompute to exercise this pipeline.

    weather_year_end lets a caller pin an exact historical window instead
    of "the last N days from today": scripts/build_scenarios.py passes
    date(2025, 12, 31) with the default 365-day window, reproducing
    khavda's exact, already-committed 2025-01-01..2025-12-31 window
    unchanged, regardless of when this function actually runs.

    generate_scenarios_yaml=True (the default, used for every real new
    site) writes a fresh {scenarios_dir}/scenarios.yaml with generic,
    site-agnostic descriptions. Khavda's legacy script passes False and
    instead patches its own hand-written config/scenarios.yaml in place
    afterward (see scripts/build_scenarios.py), preserving khavda-specific
    wording exactly as committed.
    """
    if weather_year_end is None:
        weather_year_end = date.today() - timedelta(days=1)
    weather_start = weather_year_end - timedelta(days=weather_window_days - 1)

    cache_dir = REPO_ROOT / "data" / "raw"
    progress_cb(
        "fetching_weather",
        f"Fetching/loading cached weather for {site.name} ({weather_start.isoformat()} .. {weather_year_end.isoformat()})...",
    )
    weather_df = fetch_weather(
        site.lat, site.lon, weather_start.isoformat(), weather_year_end.isoformat(), cache_dir=cache_dir
    )
    progress_cb("fetching_weather", f"{len(weather_df)} hourly rows loaded.")

    progress_cb("computing_pv_wind", "Computing PV/wind actuals...")
    pv_actual_kw = pv_power_kw(weather_df, site.pv, site.lat, site.lon, site.elevation_m, site.timezone)
    wind_actual_kw = wind_power_kw(weather_df, site.wind)

    progress_cb("selecting_scenario_windows", "Selecting S1/S2/S3 windows...")
    diag = _weekly_diagnostics(pv_actual_kw, wind_actual_kw)
    s1_row, s2_row = _select_scenario_weeks(diag)

    windows: dict[str, tuple[str, str]] = {
        "S1": (s1_row["start_date"], s1_row["end_date"]),
        "S2": (s2_row["start_date"], s2_row["end_date"]),
        "S3": (s1_row["start_date"], s1_row["end_date"]),
    }

    scenarios_dir.mkdir(parents=True, exist_ok=True)

    dataframes: dict[str, pd.DataFrame] = {}
    scenario_meta: dict[str, ScenarioWindow] = {}
    for scenario_id, (start, end) in windows.items():
        progress_cb("writing_scenario_parquets", f"Writing {scenario_id} ({start} .. {end})...")
        df = _build_scenario_parquet(scenario_id, start, end, weather_df, pv_actual_kw, wind_actual_kw, scenarios_dir)
        dataframes[scenario_id] = df
        scenario_meta[scenario_id] = ScenarioWindow(
            scenario_id=scenario_id,
            name=_GENERIC_NAMES[scenario_id],
            start_date=start,
            end_date=end,
            description=_GENERIC_DESCRIPTIONS[scenario_id],
            diesel_price_multiplier=_SCENARIO_DIESEL_MULTIPLIER[scenario_id],
            load_multiplier=SCENARIO_LOAD_MULTIPLIER[scenario_id],
            k_uncertainty=1.0,
        )

    if generate_scenarios_yaml:
        progress_cb("writing_scenarios_yaml", f"Writing {scenarios_dir / 'scenarios.yaml'}...")
        _write_scenarios_yaml(scenarios_dir / "scenarios.yaml", scenario_meta)

    progress_cb("scenarios_complete", "Scenario build complete.")
    return ScenariosBuildResult(
        scenario_windows=scenario_meta,
        scenario_dataframes=dataframes,
        weather_rows=len(weather_df),
        weekly_diagnostics=diag,
    )


# ---------------------------------------------------------------------------
# Precompute: all 4 policies x every scenario parquet present
# ---------------------------------------------------------------------------


def precompute_site_runs(
    site_id: str,
    site: SiteConfig,
    scenarios_dir: Path,
    runs_dir: Path,
    mpc_time_limit_s: float = 3.0,
    scenarios_yaml_path: Path | None = None,
    progress_cb: ProgressCallback = _noop_progress,
) -> PrecomputeResult:
    """Run all 4 policies over every scenario parquet present in
    scenarios_dir, discovered by globbing *.parquet -- NEVER hardcoded to
    exactly S1/S2/S3, so this works correctly for a reduced-scope test
    fixture (2-3 scenarios) exactly the same way it works for a full
    3-scenario site build. Writes {runs_dir}/{scenario_id}_{policy}.json
    for each. Scenario metadata (diesel_price_multiplier etc.) comes from
    scenarios_yaml_path when given, else {scenarios_dir}/scenarios.yaml --
    a parquet with no matching entry there still gets a run, using neutral
    defaults (multiplier=1.0), rather than being silently skipped.

    scenarios_yaml_path exists because khavda's legacy layout keeps its
    scenario DATA (parquets) under data/processed/ but its scenario
    METADATA (diesel_price_multiplier, descriptions, ...) at the separate,
    hand-written config/scenarios.yaml -- scripts/precompute_runs.py passes
    that path explicitly. Every other site keeps both together under its
    own scenarios_dir, so the default (None -> scenarios_dir/scenarios.yaml)
    is correct for them without needing this parameter at all.
    """
    if scenarios_yaml_path is None:
        scenarios_yaml_path = scenarios_dir / "scenarios.yaml"
    scenario_cfgs = load_scenarios_yaml(scenarios_yaml_path)
    scenario_ids = sorted(p.stem for p in scenarios_dir.glob("*.parquet"))

    runs_dir.mkdir(parents=True, exist_ok=True)

    runs: dict[tuple[str, str], RunResult] = {}
    for scenario_id in scenario_ids:
        scenario_cfg = scenario_cfgs.get(scenario_id, {"diesel_price_multiplier": 1.0})
        parquet_path = scenarios_dir / f"{scenario_id}.parquet"
        for policy in POLICIES:
            progress_cb(f"precomputing_{policy}_{scenario_id}", f"Running {scenario_id}/{policy}...")
            run = run_scenario_policy(
                scenario_id, policy, site, scenario_cfg, mpc_time_limit_s=mpc_time_limit_s, parquet_path=parquet_path
            )
            runs[(scenario_id, policy)] = run
            (runs_dir / f"{scenario_id}_{policy}.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")

    progress_cb("precompute_complete", "Precompute complete.")
    return PrecomputeResult(runs=runs)
