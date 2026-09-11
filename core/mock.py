"""
DIYA core/mock.py — deterministic fake-data generator for RunResult objects.

Produces plausible-looking (but entirely fake) 168-hour dispatch runs for the
three scenarios x four policies in config/scenarios.yaml, so that api/main.py
and the web frontend have something realistic to render before any real
weather fetching, forecasting, or optimization exists (those are later
phases). No optimization logic lives here — this is a hand-rolled heuristic
generator, not a solver.

UNIT CONVENTION: timestep is exactly 1 hour. All power is kW, all energy is
kWh; kW and kWh are numerically identical per step.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from core.types import (
    REASON_CODES,
    KPI,
    Provenance,
    RunResult,
    StepResult,
)

# ---------------------------------------------------------------------------
# Site constants mirrored from config/site_khavda.yaml (mock generator does
# not read the YAML at runtime, to stay a self-contained, dependency-free
# fake-data generator). Keep these in sync with config/site_khavda.yaml.
# ---------------------------------------------------------------------------

SITE_ID = "khavda_01"
SITE_NAME = "Khavda Off-Grid Microgrid"

BATTERY_CAPACITY_KWH = 120.0
SOC_MIN_PCT = 20.0
SOC_MAX_PCT = 95.0
SOC_INIT_PCT = 60.0
BATT_P_CHARGE_MAX_KW = 40.0
BATT_P_DISCHARGE_MAX_KW = 40.0
ETA_CHARGE = 0.95
ETA_DISCHARGE = 0.95

DIESEL_RATED_KW = 25.0
DIESEL_MIN_LOAD_FRAC = 0.30
DIESEL_FUEL_A_L_PER_KW_H = 0.08
DIESEL_FUEL_B_L_PER_KWH = 0.25
DIESEL_START_COST_INR = 150.0
DIESEL_CO2_KG_PER_L = 2.68

DIESEL_PRICE_INR_PER_L = 92.5
DIESEL_PRICE_SOURCE = "PPAC Gujarat retail selling price — PLACEHOLDER, verify before demo"
CO2_PRICE_INR_PER_KG = 2.0
VOLL_CRITICAL_INR_PER_KWH = 500.0
VOLL_ESSENTIAL_INR_PER_KWH = 60.0
VOLL_DEFERRABLE_INR_PER_KWH = 12.0

RESERVE_HOURS = 3.0

# ---------------------------------------------------------------------------
# Scenario / policy metadata mirrored from config/scenarios.yaml. Keep in
# sync with that file.
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict] = {
    "S1": {"name": "Normal week", "diesel_price_multiplier": 1.0, "load_multiplier": 1.0},
    "S2": {"name": "Monsoon stretch", "diesel_price_multiplier": 1.0, "load_multiplier": 1.0},
    "S3": {"name": "Lean season", "diesel_price_multiplier": 1.4, "load_multiplier": 1.2},
}

POLICIES: list[str] = ["diesel_only", "rule_based", "mpc", "perfect_foresight"]

# Policy behavior knobs: the SOC% at/below which the policy allows diesel to
# turn on to cover a deficit, and the SOC% at/above which it prefers to turn
# diesel back off (hysteresis). diesel_only ignores these and always prefers
# diesel over battery discharge.
POLICY_PARAMS: dict[str, dict] = {
    "diesel_only": {"soc_on": 70.0, "soc_off": 75.0, "always_diesel": True},
    "rule_based": {"soc_on": 35.0, "soc_off": 45.0, "always_diesel": False},
    "mpc": {"soc_on": 30.0, "soc_off": 42.0, "always_diesel": False},
    "perfect_foresight": {"soc_on": 26.0, "soc_off": 38.0, "always_diesel": False},
}

CODE_VERSION = "0.1.0-phase0"
KOLKATA_TZ = timezone(timedelta(hours=5, minutes=30))
ANCHOR_START = datetime(2026, 1, 5, 0, 0, 0, tzinfo=KOLKATA_TZ)  # arbitrary Monday


def _rng_seed(scenario_id: str, policy: str, seed: int) -> int:
    """Combine scenario/policy/seed into a single deterministic RNG seed."""
    digest = hashlib.sha256(f"{scenario_id}|{policy}|{seed}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _daily_solar_multiplier(scenario_id: str, day_idx: int, rng: np.random.Generator) -> float:
    """Per-day solar derate. S2 gets 2 consecutive low-irradiance (monsoon) days."""
    if scenario_id == "S2" and day_idx in (2, 3):
        return float(rng.uniform(0.15, 0.30))
    return float(rng.uniform(0.85, 1.05))


def _pv_kw(hour: int, day_mult: float, rng: np.random.Generator) -> float:
    """Solar bell curve peaking ~45 kW at 13:00, zero outside daylight hours."""
    if hour < 6 or hour > 19:
        return 0.0
    x = hour - 13
    gauss = math.exp(-(x**2) / (2 * 3.2**2))
    base = 45.0 * gauss * day_mult
    noise = rng.normal(0, 1.5)
    return round(max(0.0, base + noise), 3)


def _wind_kw(scenario_id: str, day_idx: int, rng: np.random.Generator) -> float:
    """Wind noise 0-8 kW, slightly boosted during the S2 monsoon days."""
    val = float(rng.uniform(0.0, 8.0))
    if scenario_id == "S2" and day_idx in (2, 3):
        val = min(8.0, val * 1.3)
    return round(val, 3)


def _loads_kw(hour: int, load_multiplier: float, rng: np.random.Generator) -> tuple[float, float, float]:
    """Critical (health centre), essential (household/business), deferrable
    (RO plant / flour mill) load with morning and evening essential peaks."""
    critical = 4.0 + 0.3 * math.sin(hour / 24 * 2 * math.pi) + rng.normal(0, 0.1)
    critical = max(3.0, critical) * load_multiplier

    morning = 9.0 * math.exp(-((hour - 8) ** 2) / (2 * 1.3**2))
    evening = 13.0 * math.exp(-((hour - 19) ** 2) / (2 * 2.0**2))
    essential = (5.0 + morning + evening + rng.normal(0, 0.3)) * load_multiplier
    essential = max(0.0, essential)

    deferrable = (8.0 if 9 <= hour < 17 else 0.0) * load_multiplier

    return round(critical, 3), round(essential, 3), round(deferrable, 3)


def _pick_reason(
    *,
    unserved_c: float,
    deferrable: float,
    unserved_d: float,
    dg_transitioned_on: bool,
    hour: int,
    policy: str,
    dg_on: int,
    dg_kw: float,
    protect_reserve: bool,
    deficit: float,
    charge_kw: float,
) -> str:
    if unserved_c > 0.01:
        return "R8_CRITICAL_DEFICIT"
    if deferrable > 0.5 and unserved_d > 0.5:
        return "R7_SHED_DEFERRABLE"
    if dg_transitioned_on and 17 <= hour <= 22 and policy != "diesel_only":
        return "R1_PREPOSITION"
    if dg_on and dg_kw <= DIESEL_RATED_KW * DIESEL_MIN_LOAD_FRAC * 1.05:
        return "R5_MINLOAD"
    if dg_on:
        return "R3_CHEAPER_DIESEL"
    if deficit > 0.05 and not protect_reserve:
        return "R4_RESERVE_HOLD"
    if deficit > 0.05:
        return "R6_AVOID_START"
    if charge_kw > 0.05:
        return "R2_SOLAR_SURPLUS"
    return "R0_NOMINAL"


def make_mock_run(scenario_id: str, policy: str, seed: int = 0, n_hours: int = 168) -> RunResult:
    """Generate a deterministic, internally-consistent fake RunResult.

    Deterministic for a given (scenario_id, policy, seed, n_hours) tuple.
    """
    if scenario_id not in SCENARIOS:
        raise ValueError(f"unknown scenario_id: {scenario_id}")
    if policy not in POLICIES:
        raise ValueError(f"unknown policy: {policy}")

    scen = SCENARIOS[scenario_id]
    params = POLICY_PARAMS[policy]
    rng = np.random.default_rng(_rng_seed(scenario_id, policy, seed))

    n_days = max(1, math.ceil(n_hours / 24))
    day_solar_mult = [_daily_solar_multiplier(scenario_id, d, rng) for d in range(n_days)]

    soc_kwh = SOC_INIT_PCT / 100 * BATTERY_CAPACITY_KWH
    dg_on_prev = 0
    dg_starts = 0

    steps: list[StepResult] = []

    for t in range(n_hours):
        day_idx = t // 24
        hour = t % 24

        pv_kw = _pv_kw(hour, day_solar_mult[day_idx], rng)
        wind_kw = _wind_kw(scenario_id, day_idx, rng)
        critical, essential, deferrable = _loads_kw(hour, scen["load_multiplier"], rng)

        renewable = pv_kw + wind_kw
        firm_load = critical + essential
        net = renewable - firm_load

        dg_on = 0
        dg_kw = 0.0
        batt_kw = 0.0
        curtailed = 0.0
        unserved_c = unserved_e = unserved_d = 0.0
        deferrable_served = 0.0
        charge_kw = 0.0
        protect_reserve = True
        deficit = 0.0

        if net >= 0:
            surplus = net
            room_kwh = max(0.0, SOC_MAX_PCT / 100 * BATTERY_CAPACITY_KWH - soc_kwh)
            charge_kw = max(0.0, min(surplus, BATT_P_CHARGE_MAX_KW, room_kwh / ETA_CHARGE if ETA_CHARGE > 0 else 0.0))
            soc_kwh += charge_kw * ETA_CHARGE
            batt_kw = -charge_kw
            leftover = surplus - charge_kw

            deferrable_served = min(deferrable, leftover)
            leftover -= deferrable_served
            unserved_d = deferrable - deferrable_served
            curtailed = max(0.0, leftover)

            soc_pct_now = soc_kwh / BATTERY_CAPACITY_KWH * 100
            if dg_on_prev and soc_pct_now >= params["soc_off"]:
                dg_on = 0
            else:
                dg_on = dg_on_prev
        else:
            deficit = -net
            soc_pct_now = soc_kwh / BATTERY_CAPACITY_KWH * 100
            usable_above_min = max(0.0, soc_kwh - SOC_MIN_PCT / 100 * BATTERY_CAPACITY_KWH)
            reserve_kwh = RESERVE_HOURS * critical
            protect_reserve = usable_above_min > reserve_kwh
            dischargeable_kwh = usable_above_min - reserve_kwh if protect_reserve else usable_above_min
            dischargeable_kwh = max(0.0, dischargeable_kwh)
            max_discharge_kw = min(BATT_P_DISCHARGE_MAX_KW, dischargeable_kwh * ETA_DISCHARGE)

            need_diesel = bool(params["always_diesel"]) or dg_on_prev == 1 or soc_pct_now <= params["soc_on"] or deficit > max_discharge_kw

            if need_diesel:
                dg_on = 1
                dg_kw = min(DIESEL_RATED_KW, max(deficit, DIESEL_RATED_KW * DIESEL_MIN_LOAD_FRAC))
                remaining = deficit - dg_kw
                if remaining > 0:
                    batt_discharge_kw = min(max_discharge_kw, remaining, soc_kwh * ETA_DISCHARGE)
                    soc_kwh -= batt_discharge_kw / ETA_DISCHARGE
                    batt_kw = batt_discharge_kw
                    remaining -= batt_discharge_kw
                else:
                    overprod = -remaining
                    room_kwh = max(0.0, SOC_MAX_PCT / 100 * BATTERY_CAPACITY_KWH - soc_kwh)
                    charge_kw = max(0.0, min(overprod, BATT_P_CHARGE_MAX_KW, room_kwh / ETA_CHARGE if ETA_CHARGE > 0 else 0.0))
                    soc_kwh += charge_kw * ETA_CHARGE
                    batt_kw = -charge_kw
                    remaining = -(overprod - charge_kw)
                    curtailed = max(0.0, overprod - charge_kw)

                if remaining > 1e-6:
                    unserved_c = min(critical, remaining)
                    remaining -= unserved_c
                    unserved_e = max(0.0, remaining)
                unserved_d = deferrable
            else:
                dg_on = 0
                batt_discharge_kw = min(max_discharge_kw, deficit, soc_kwh * ETA_DISCHARGE)
                soc_kwh -= batt_discharge_kw / ETA_DISCHARGE
                batt_kw = batt_discharge_kw
                remaining = deficit - batt_discharge_kw
                if remaining > 1e-6:
                    unserved_e = min(essential, remaining)
                    remaining -= unserved_e
                    unserved_c = max(0.0, remaining)
                unserved_d = deferrable

        soc_kwh = min(max(soc_kwh, SOC_MIN_PCT / 100 * BATTERY_CAPACITY_KWH), SOC_MAX_PCT / 100 * BATTERY_CAPACITY_KWH)
        soc_pct = soc_kwh / BATTERY_CAPACITY_KWH * 100

        dg_transitioned_on = dg_on == 1 and dg_on_prev == 0
        if dg_transitioned_on:
            dg_starts += 1

        fuel_l = round(DIESEL_FUEL_A_L_PER_KW_H * DIESEL_RATED_KW + DIESEL_FUEL_B_L_PER_KWH * dg_kw, 4) if dg_on else 0.0

        reason_code = _pick_reason(
            unserved_c=unserved_c,
            deferrable=deferrable,
            unserved_d=unserved_d,
            dg_transitioned_on=dg_transitioned_on,
            hour=hour,
            policy=policy,
            dg_on=dg_on,
            dg_kw=dg_kw,
            protect_reserve=protect_reserve,
            deficit=deficit,
            charge_kw=charge_kw,
        )

        ts = ANCHOR_START + timedelta(hours=t)
        steps.append(
            StepResult(
                t=ts.isoformat(),
                pv_kw=pv_kw,
                wind_kw=wind_kw,
                dg_kw=round(dg_kw, 3),
                dg_on=dg_on,
                batt_kw=round(batt_kw, 3),
                soc_kwh=round(soc_kwh, 3),
                soc_pct=round(soc_pct, 3),
                load_critical_kw=critical,
                load_essential_kw=essential,
                load_deferrable_kw=deferrable,
                unserved_critical_kwh=round(unserved_c, 4),
                unserved_essential_kwh=round(unserved_e, 4),
                unserved_deferrable_kwh=round(unserved_d, 4),
                curtailed_kwh=round(curtailed, 4),
                fuel_l=fuel_l,
                reason_code=reason_code,
                reason_text=REASON_CODES[reason_code],
            )
        )

        dg_on_prev = dg_on

    kpi = _compute_kpi(steps, scen, policy, rng)
    provenance = Provenance(
        weather_source="mock",
        forecast_method="mock",
        load_method="mock",
        diesel_price_source=DIESEL_PRICE_SOURCE,
        config_hash=_mock_config_hash(),
        generated_at=datetime.now(timezone.utc).isoformat(),
        code_version=CODE_VERSION,
    )

    return RunResult(
        scenario_id=scenario_id,
        policy=policy,  # type: ignore[arg-type]
        site_id=SITE_ID,
        site_name=SITE_NAME,
        steps=steps,
        kpi=kpi,
        provenance=provenance,
        notes="Phase 0 mock data — not a real dispatch solve.",
    )


def _compute_kpi(steps: list[StepResult], scen: dict, policy: str, rng: np.random.Generator) -> KPI:
    diesel_price = DIESEL_PRICE_INR_PER_L * scen["diesel_price_multiplier"]

    diesel_l = sum(s.fuel_l for s in steps)
    cost_fuel_inr = diesel_l * diesel_price
    co2_kg = diesel_l * DIESEL_CO2_KG_PER_L

    renewable_used = sum(s.pv_kw + s.wind_kw - s.curtailed_kwh for s in steps)
    renewable_used = max(0.0, renewable_used)
    dg_energy = sum(s.dg_kw for s in steps)
    denom = renewable_used + dg_energy
    renewable_frac = renewable_used / denom if denom > 0 else 1.0

    unserved_critical_kwh = sum(s.unserved_critical_kwh for s in steps)
    unserved_total_kwh = sum(
        s.unserved_critical_kwh + s.unserved_essential_kwh + s.unserved_deferrable_kwh for s in steps
    )
    critical_outage_hours = sum(1 for s in steps if s.unserved_critical_kwh > 0.01)

    dg_starts = sum(1 for i, s in enumerate(steps) if s.dg_on == 1 and (i == 0 or steps[i - 1].dg_on == 0))
    dg_run_hours = sum(1 for s in steps if s.dg_on == 1)

    total_discharge_kwh = sum(max(0.0, s.batt_kw) for s in steps)
    batt_equivalent_full_cycles = total_discharge_kwh / BATTERY_CAPACITY_KWH

    total_load_kwh = sum(s.load_critical_kw + s.load_essential_kw + s.load_deferrable_kw for s in steps)
    served_kwh = max(0.0, total_load_kwh - unserved_total_kwh)

    cost_total_inr = (
        cost_fuel_inr
        + co2_kg * CO2_PRICE_INR_PER_KG
        + dg_starts * DIESEL_START_COST_INR
        + unserved_critical_kwh * VOLL_CRITICAL_INR_PER_KWH
        + sum(s.unserved_essential_kwh for s in steps) * VOLL_ESSENTIAL_INR_PER_KWH
        + sum(s.unserved_deferrable_kwh for s in steps) * VOLL_DEFERRABLE_INR_PER_KWH
    )
    cost_per_delivered_kwh_inr = cost_total_inr / served_kwh if served_kwh > 0 else 0.0

    # Fabricated but plausible solve-time stand-ins: diesel_only/rule_based
    # are direct rule evaluation (no MILP), mpc/perfect_foresight represent
    # a MILP solve that Phase 0 does not actually run.
    if policy in ("mpc", "perfect_foresight"):
        samples = rng.uniform(40.0, 320.0, size=24)
    else:
        samples = rng.uniform(0.05, 0.6, size=24)
    solve_ms_mean = float(np.mean(samples))
    solve_ms_p95 = float(np.percentile(samples, 95))

    return KPI(
        diesel_l=round(diesel_l, 3),
        cost_fuel_inr=round(cost_fuel_inr, 2),
        cost_total_inr=round(cost_total_inr, 2),
        co2_kg=round(co2_kg, 3),
        renewable_frac=round(renewable_frac, 4),
        unserved_critical_kwh=round(unserved_critical_kwh, 4),
        unserved_total_kwh=round(unserved_total_kwh, 4),
        critical_outage_hours=float(critical_outage_hours),
        dg_starts=dg_starts,
        dg_run_hours=float(dg_run_hours),
        batt_equivalent_full_cycles=round(batt_equivalent_full_cycles, 3),
        cost_per_delivered_kwh_inr=round(cost_per_delivered_kwh_inr, 3),
        served_kwh=round(served_kwh, 3),
        solve_ms_mean=round(solve_ms_mean, 3),
        solve_ms_p95=round(solve_ms_p95, 3),
    )


def _mock_config_hash() -> str:
    """8-hex-char hash of the mock module's mirrored site constants.

    Distinct from core.types.config_hash(SiteConfig) — this is a
    provenance breadcrumb for mock data only, so mock RunResults are
    traceable to the constants above without re-reading the YAML.
    """
    payload = json.dumps(
        {
            "site_id": SITE_ID,
            "battery_capacity_kwh": BATTERY_CAPACITY_KWH,
            "diesel_rated_kw": DIESEL_RATED_KW,
            "diesel_price_inr_per_l": DIESEL_PRICE_INR_PER_L,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def write_mock_files(outdir: str | Path, seed: int = 0) -> list[Path]:
    """Write all 12 (scenario x policy) mock RunResult JSON files to outdir.

    Files are named `{scenario}_{policy}.json`. Returns the list of paths
    written.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for scenario_id in SCENARIOS:
        for policy in POLICIES:
            run = make_mock_run(scenario_id, policy, seed=seed)
            path = outdir / f"{scenario_id}_{policy}.json"
            path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
            written.append(path)
    return written


if __name__ == "__main__":
    for target in ("web/public/runs", "data/processed/mock"):
        paths = write_mock_files(target)
        print(f"wrote {len(paths)} files to {target}")
