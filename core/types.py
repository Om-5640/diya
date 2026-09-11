"""
DIYA — Dispatch Intelligence for Yield & Autonomy
core/types.py — data contracts for the microgrid dispatch optimizer.

UNIT CONVENTION: timestep is exactly 1 hour. All power is kW. All energy is kWh.
Because dt = 1h, kW and kWh are numerically identical per step. Never introduce
another timestep. Every field name carries its unit suffix (_kw, _kwh, _l, _inr,
_kg, _pct).

This module defines pydantic v2 models only. No optimization, forecasting, or
dispatch logic lives here — see core/milp.py, core/policies.py, core/simulator.py
for those (later phases).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Reason codes — operator-facing explanations attached to every dispatch step.
# ---------------------------------------------------------------------------

REASON_CODES: dict[str, str] = {
    "R1_PREPOSITION": (
        "Starting diesel early — low solar and wind expected this evening; "
        "preserving battery for the health centre."
    ),
    "R2_SOLAR_SURPLUS": "Charging the battery from surplus solar and wind.",
    "R3_CHEAPER_DIESEL": (
        "Diesel is cheaper right now than discharging the battery further at "
        "the configured costs."
    ),
    "R4_RESERVE_HOLD": (
        "Holding energy in reserve — that is the health centre's supply for "
        "the next few hours."
    ),
    "R5_MINLOAD": "Diesel held at minimum load — running it lower would waste fuel.",
    "R6_AVOID_START": "Avoiding a generator start; the battery covers this gap.",
    "R7_SHED_DEFERRABLE": (
        "Deferring the RO water plant / flour mill to protect essential supply."
    ),
    "R8_CRITICAL_DEFICIT": (
        "WARNING: health-centre demand cannot be fully met with available "
        "capacity. This is a sizing shortfall, not a dispatch choice."
    ),
    "R0_NOMINAL": "Renewables are covering demand.",
}


# ---------------------------------------------------------------------------
# Equipment / site specification models
# ---------------------------------------------------------------------------


class BatterySpec(BaseModel):
    """Battery energy storage spec. Power in kW, energy in kWh, dt = 1h."""

    capacity_kwh: float
    soc_min_pct: float
    soc_max_pct: float
    p_charge_max_kw: float
    p_discharge_max_kw: float
    eta_charge: float
    eta_discharge: float
    soc_init_pct: float


class DieselSpec(BaseModel):
    """Diesel genset spec. Power in kW, fuel in L, dt = 1h."""

    rated_kw: float
    min_load_frac: float
    fuel_a_l_per_kw_h: float  # idle/no-load fuel coefficient
    fuel_b_l_per_kwh: float  # marginal fuel coefficient
    start_cost_inr: float
    co2_kg_per_l: float


class PVSpec(BaseModel):
    """Photovoltaic array spec. Power in kW, dt = 1h."""

    capacity_kwp: float
    tilt_deg: float
    azimuth_deg: float
    temp_coeff_per_c: float
    derate: float


class WindSpec(BaseModel):
    """Wind turbine spec. Power in kW, dt = 1h."""

    rated_kw: float
    hub_height_m: float
    cut_in_ms: float
    rated_ms: float
    cut_out_ms: float
    shear_exp: float


class EconomicsSpec(BaseModel):
    """Economic parameters. Prices in INR, energy in kWh, mass in kg."""

    diesel_price_inr_per_l: float
    diesel_price_source: str
    co2_price_inr_per_kg: float
    voll_critical_inr_per_kwh: float
    voll_essential_inr_per_kwh: float
    voll_deferrable_inr_per_kwh: float


class SiteConfig(BaseModel):
    """Full site configuration. dt = 1h throughout; power kW, energy kWh."""

    site_id: str
    name: str
    lat: float
    lon: float
    elevation_m: float
    timezone: str

    pv: PVSpec
    wind: WindSpec
    battery: BatterySpec
    diesel: DieselSpec
    economics: EconomicsSpec

    horizon_hours: int = 24
    reserve_hours: int = 3
    k_uncertainty: float = 1.0


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class StepResult(BaseModel):
    """One hourly dispatch step. Power in kW, energy in kWh, fuel in L."""

    t: str  # ISO 8601 timestamp
    pv_kw: float
    wind_kw: float
    dg_kw: float
    dg_on: int  # 0 or 1
    batt_kw: float  # positive = discharge, negative = charge
    soc_kwh: float
    soc_pct: float
    load_critical_kw: float
    load_essential_kw: float
    load_deferrable_kw: float
    unserved_critical_kwh: float
    unserved_essential_kwh: float
    unserved_deferrable_kwh: float
    curtailed_kwh: float
    fuel_l: float
    reason_code: str
    reason_text: str


class KPI(BaseModel):
    """Run-level key performance indicators."""

    diesel_l: float
    cost_fuel_inr: float
    cost_total_inr: float
    co2_kg: float
    renewable_frac: float
    unserved_critical_kwh: float
    unserved_total_kwh: float
    critical_outage_hours: float
    dg_starts: int
    dg_run_hours: float
    batt_equivalent_full_cycles: float
    cost_per_delivered_kwh_inr: float
    served_kwh: float
    solve_ms_mean: float
    solve_ms_p95: float


class Provenance(BaseModel):
    """Data lineage / reproducibility metadata for a run."""

    weather_source: str
    forecast_method: str
    load_method: str
    diesel_price_source: str
    config_hash: str
    generated_at: str
    code_version: str


class RunResult(BaseModel):
    """A complete dispatch run: steps, KPIs, and provenance."""

    scenario_id: str
    policy: Literal["diesel_only", "rule_based", "mpc", "perfect_foresight"]
    site_id: str
    site_name: str
    steps: list[StepResult]
    kpi: KPI
    provenance: Provenance
    notes: str = ""


class DispatchPlan(BaseModel):
    """Output of a single-horizon MILP solve. Power kW, energy kWh, fuel L."""

    horizon_hours: int
    pv_use_kw: list[float]
    wind_use_kw: list[float]
    dg_kw: list[float]
    dg_on: list[int]  # 0/1
    dg_start: list[int]  # 0/1
    p_charge_kw: list[float]
    p_discharge_kw: list[float]
    soc_kwh: list[float]  # end-of-step SOC, length == horizon_hours
    unserved_critical_kwh: list[float]
    unserved_essential_kwh: list[float]
    unserved_deferrable_kwh: list[float]
    curtailed_kwh: list[float]
    fuel_l: list[float]
    solve_status: str  # "Optimal" | "Infeasible" | "Timeout" | "Error"
    solve_ms: float
    objective_value: float


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_site_config(path: str | Path) -> SiteConfig:
    """Load and validate a SiteConfig from YAML, attaching a stable config_hash.

    The hash is sha256 of the sorted-key JSON dump of the validated config
    (excluding the hash itself), truncated to the first 8 hex characters.
    Callers that need the hash should use `config_hash(config)` below; this
    loader just validates the file parses into a SiteConfig.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return SiteConfig.model_validate(raw)


def config_hash(config: SiteConfig) -> str:
    """Stable sha256-derived hash (first 8 hex chars) of a SiteConfig."""
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:8]
