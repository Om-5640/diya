"""
DIYA core/kpi.py — KPI computation from a completed list[StepResult].

Every policy's cost_total_inr is computed with the identical formula (fuel +
CO2 + diesel starts + value-of-lost-load), whether or not that policy used
the optimizer, so all four policies are directly cost-comparable.

DEVIATION NOTE: `battery_capacity_kwh` is an explicit parameter here (the
spec's prose says "pass battery capacity in as a parameter" but the given
signature omitted it) — batt_equivalent_full_cycles is uncomputable without
it.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW, fuel in L.
"""

from __future__ import annotations

import numpy as np

from core.types import KPI, DieselSpec, EconomicsSpec, StepResult


def compute_kpi(
    steps: list[StepResult],
    diesel: DieselSpec,
    economics: EconomicsSpec,
    battery_capacity_kwh: float,
    solve_ms_list: list[float] | None = None,
) -> KPI:
    diesel_l = sum(s.fuel_l for s in steps)
    cost_fuel_inr = economics.diesel_price_inr_per_l * diesel_l
    co2_kg = diesel.co2_kg_per_l * diesel_l
    cost_co2_inr = economics.co2_price_inr_per_kg * co2_kg

    dg_starts = 0
    prev_on = 0
    for s in steps:
        if s.dg_on == 1 and prev_on == 0:
            dg_starts += 1
        prev_on = s.dg_on
    cost_start_inr = diesel.start_cost_inr * dg_starts

    cost_voll_inr = sum(
        economics.voll_critical_inr_per_kwh * s.unserved_critical_kwh
        + economics.voll_essential_inr_per_kwh * s.unserved_essential_kwh
        + economics.voll_deferrable_inr_per_kwh * s.unserved_deferrable_kwh
        for s in steps
    )

    cost_total_inr = cost_fuel_inr + cost_co2_inr + cost_start_inr + cost_voll_inr

    served_kwh = sum(
        (s.load_critical_kw + s.load_essential_kw + s.load_deferrable_kw)
        - (s.unserved_critical_kwh + s.unserved_essential_kwh + s.unserved_deferrable_kwh)
        for s in steps
    )

    sum_pv = sum(s.pv_kw for s in steps)
    sum_wind = sum(s.wind_kw for s in steps)
    sum_dg = sum(s.dg_kw for s in steps)
    renewable_frac = (sum_pv + sum_wind) / max(1e-9, sum_pv + sum_wind + sum_dg)

    unserved_critical_kwh = sum(s.unserved_critical_kwh for s in steps)
    unserved_total_kwh = sum(
        s.unserved_critical_kwh + s.unserved_essential_kwh + s.unserved_deferrable_kwh for s in steps
    )
    critical_outage_hours = sum(1 for s in steps if s.unserved_critical_kwh > 1e-6)

    dg_run_hours = sum(s.dg_on for s in steps)

    batt_equivalent_full_cycles = sum(max(0.0, s.batt_kw) for s in steps) / battery_capacity_kwh

    cost_per_delivered_kwh_inr = cost_total_inr / max(1e-9, served_kwh)

    if solve_ms_list:
        arr = np.array(solve_ms_list, dtype=float)
        solve_ms_mean = float(np.mean(arr))
        solve_ms_p95 = float(np.percentile(arr, 95))
    else:
        solve_ms_mean = 0.0
        solve_ms_p95 = 0.0

    return KPI(
        diesel_l=diesel_l,
        cost_fuel_inr=cost_fuel_inr,
        cost_total_inr=cost_total_inr,
        co2_kg=co2_kg,
        renewable_frac=renewable_frac,
        unserved_critical_kwh=unserved_critical_kwh,
        unserved_total_kwh=unserved_total_kwh,
        critical_outage_hours=float(critical_outage_hours),
        dg_starts=dg_starts,
        dg_run_hours=float(dg_run_hours),
        batt_equivalent_full_cycles=batt_equivalent_full_cycles,
        cost_per_delivered_kwh_inr=cost_per_delivered_kwh_inr,
        served_kwh=served_kwh,
        solve_ms_mean=solve_ms_mean,
        solve_ms_p95=solve_ms_p95,
    )
