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
#
# BUGFIX-1: these were originally a static dict of khavda-flavored final
# text ("the health centre", "the RO water plant / flour mill" baked in),
# which is actively misleading for a non-village site (e.g. a college
# campus). REASON_CODES now holds TEMPLATES (str.format placeholders);
# reason_text_for() substitutes each SiteConfig's own
# critical_load_label/deferrable_load_label to produce the final text.
# Khavda's config sets those labels to its existing wording exactly, so its
# reason_text output is byte-identical to before this refactor -- verified
# directly against its precomputed runs, not assumed.
# ---------------------------------------------------------------------------

REASON_CODES: dict[str, str] = {
    "R1_PREPOSITION": (
        "Starting diesel early — low solar and wind expected this evening; "
        "preserving battery for {critical_load_label}."
    ),
    "R2_SOLAR_SURPLUS": "Charging the battery from surplus solar and wind.",
    "R3_CHEAPER_DIESEL": (
        "Diesel is cheaper right now than discharging the battery further at "
        "the configured costs."
    ),
    "R4_RESERVE_HOLD": (
        "Holding energy in reserve — that is {critical_load_label}'s supply for "
        "the next few hours."
    ),
    "R5_MINLOAD": "Diesel held at minimum load — running it lower would waste fuel.",
    "R6_AVOID_START": "Avoiding a generator start; the battery covers this gap.",
    "R7_SHED_DEFERRABLE": (
        "Deferring {deferrable_load_label} to protect essential supply."
    ),
    "R8_CRITICAL_DEFICIT": (
        "WARNING: {critical_load_adjective} demand cannot be fully met with available "
        "capacity. This is a sizing shortfall, not a dispatch choice."
    ),
    "R0_NOMINAL": "Renewables are covering demand.",
    "R9_DIESEL_ONLY": (
        "Diesel running continuously — this baseline does not use solar, wind or battery."
    ),
}


def _adjective_form(label: str) -> str:
    """"the health centre" -> "health-centre": strips a leading "the "/"The "
    and joins the remaining words with hyphens. Used only for
    R8_CRITICAL_DEFICIT's hyphenated-compound-adjective phrasing
    ("{X} demand"), the one template whose original khavda wording doesn't
    use the plain "the X" noun-phrase form the other templates share."""
    for prefix in ("The ", "the "):
        if label.startswith(prefix):
            label = label[len(prefix) :]
            break
    return label.replace(" ", "-")


def reason_text_for(code: str, site: "SiteConfig") -> str:
    """The final, site-flavored reason_text for `code` -- substitutes
    site.critical_load_label/deferrable_load_label into REASON_CODES'
    template for that code."""
    return REASON_CODES[code].format(
        critical_load_label=site.critical_load_label,
        deferrable_load_label=site.deferrable_load_label,
        critical_load_adjective=_adjective_form(site.critical_load_label),
    )


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

    # BUGFIX-1: narrative labels substituted into REASON_CODES' templates
    # (see reason_text_for below) so reason_text reads naturally for any
    # site, not just Khavda's village-specific nouns. Defaults are
    # deliberately generic; khavda's config sets these explicitly to its
    # existing flavor text ("the health centre" / "the RO water plant /
    # flour mill") so its reason_text output is unchanged.
    critical_load_label: str = "critical load"
    deferrable_load_label: str = "deferrable load"


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
# API request models
# ---------------------------------------------------------------------------


class LiveSolveRequest(BaseModel):
    """Body for POST /api/solve_live."""

    lat: float
    lon: float
    soc_pct: float


class ResolveWeights(BaseModel):
    """Relative cost-tradeoff weights for POST /api/resolve, each 1.0 =
    the site's configured default."""

    cost: float = 1.0
    co2: float = 1.0
    reliability: float = 1.0


class ResolveRequest(BaseModel):
    """Body for POST /api/resolve."""

    scenario_id: str
    weights: ResolveWeights = ResolveWeights()
    k_uncertainty: float
    diesel_price_inr_per_l: float


# ---------------------------------------------------------------------------
# Multi-site store models (Phase C of DIYA v2)
# ---------------------------------------------------------------------------


class SiteRecord(BaseModel):
    """One entry in sites/registry.json. Points at a SiteConfig YAML plus its
    scenarios/runs directories -- does not embed the config itself."""

    site_id: str
    display_name: str
    lat: float
    lon: float
    config_path: str
    scenarios_dir: str
    runs_dir: str
    created_at: str  # ISO 8601
    is_seed: bool = False


class NewSiteRequest(BaseModel):
    """Body for POST /api/sites. Minimal input; SiteStore.create derives a
    full SiteConfig (PV tilt/azimuth by hemisphere, battery/diesel/economics
    defaults, etc.) from these fields -- see core/site_store.py."""

    display_name: str
    lat: float
    lon: float
    elevation_m: float = 0.0
    timezone: str = "Asia/Kolkata"
    pv_capacity_kwp: float
    battery_capacity_kwh: float
    diesel_rated_kw: float = 0.0
    outage_cost_inr_per_kwh: float = 500.0
    diesel_price_inr_per_l: float = 92.5


class SiteSummary(BaseModel):
    """Response shape for GET /api/sites and GET /api/sites/{site_id}."""

    site_id: str
    display_name: str
    lat: float
    lon: float
    created_at: str
    is_seed: bool
    # BUGFIX-2: added so Manage can show the site's current diesel price
    # without a separate call -- additive, does not change any other field.
    diesel_price_inr_per_l: float


# ---------------------------------------------------------------------------
# Methodology (DIYA v2 Phase C.6) — general, site-independent data
# provenance categorization for GET /api/methodology. This is the SAME
# categorization already used in config/site_khavda.yaml's comment block,
# restructured as data rather than duplicated: define it once, here, and
# have the endpoint return this constant directly.
# ---------------------------------------------------------------------------


class MethodologyCategory(BaseModel):
    label: str
    items: list[str]


class MethodologyNotes(BaseModel):
    categories: list[MethodologyCategory]
    load_note: str


METHODOLOGY_NOTES = MethodologyNotes(
    categories=[
        MethodologyCategory(
            label="Measured / Live",
            items=["Historical and forecast weather (Open-Meteo)"],
        ),
        MethodologyCategory(
            label="Public Reference",
            items=["Diesel CO2 factor, PV/wind physics constants"],
        ),
        MethodologyCategory(
            label="Engineering Assumption",
            items=[
                "Diesel price default, VOLL values, fuel curve coefficients, "
                "load profile shape -- user-editable per site"
            ],
        ),
    ],
    load_note="Load is a synthetic, seeded model -- not metered telemetry.",
)


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
