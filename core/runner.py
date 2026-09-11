"""
DIYA core/runner.py — end-to-end run orchestrator: config + scenario +
policy -> RunResult.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW, fuel in L.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.kpi import compute_kpi
from core.policies import run_diesel_only, run_mpc, run_perfect_foresight, run_rule_based
from core.types import Provenance, RunResult, SiteConfig, config_hash

REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

CODE_VERSION = "phase3"


def run_scenario_policy(
    scenario_id: str,
    policy: str,
    site: SiteConfig,
    scenario_cfg: dict,
    max_hours: int | None = None,
    mpc_time_limit_s: float = 3.0,
) -> RunResult:
    """Load scenario data, apply the scenario's diesel_price_multiplier to
    a copy of site.economics (load_multiplier is already baked into the
    parquet from Phase 1A -- not reapplied here), dispatch to the matching
    policy, compute KPIs, and wrap the result into a RunResult.

    `mpc_time_limit_s` is a passthrough to run_mpc's per-hour rolling
    solves only (not perfect_foresight's single solve) -- tests pass a
    smaller value (2.0) to keep a 168-hour, up-to-168-solve mpc run fast;
    scripts/precompute_runs.py uses the 3.0 default.
    """
    parquet_path = PROCESSED_DIR / f"{scenario_id}.parquet"
    df = pd.read_parquet(parquet_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    adjusted_economics = site.economics.model_copy(
        update={
            "diesel_price_inr_per_l": (
                site.economics.diesel_price_inr_per_l * scenario_cfg["diesel_price_multiplier"]
            )
        }
    )
    adjusted_site = site.model_copy(update={"economics": adjusted_economics})

    if policy == "diesel_only":
        steps = run_diesel_only(df, adjusted_site, max_hours=max_hours)
        solve_ms_list = None
    elif policy == "rule_based":
        steps = run_rule_based(df, adjusted_site, max_hours=max_hours)
        solve_ms_list = None
    elif policy == "mpc":
        steps, solve_ms_list = run_mpc(df, adjusted_site, max_hours=max_hours, time_limit_s=mpc_time_limit_s)
    elif policy == "perfect_foresight":
        steps, solve_ms_list = run_perfect_foresight(df, adjusted_site, max_hours=max_hours)
    else:
        raise ValueError(f"unknown policy: {policy}")

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
        diesel_price_source=site.economics.diesel_price_source,
        config_hash=config_hash(site),
        generated_at=datetime.now(timezone.utc).isoformat(),
        code_version=CODE_VERSION,
    )

    return RunResult(
        scenario_id=scenario_id,
        policy=policy,
        site_id=site.site_id,
        site_name=site.name,
        steps=steps,
        kpi=kpi,
        provenance=provenance,
        notes="",
    )
