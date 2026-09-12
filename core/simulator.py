"""
DIYA core/simulator.py — the shared physics resolver.

Turns "diesel on or off this hour" plus REALIZED weather/load into an
actual, physically consistent StepResult, via a deterministic closed-form
calculation (no LP). This is the one place battery/diesel/shedding physics
live outside the MILP — both rule_based and mpc call `simulate_hour`, so
its behavior only needs to be correct once. `simulate_hour_diesel_only` is
a separate, standalone baseline that does not call `simulate_hour` at all
(it deliberately never touches renewables or the battery).

reason_code/reason_text are intentionally left blank ("") by both
functions here — only the calling policy (core/policies.py) knows whether
a dispatch decision came from a reactive threshold rule or an MPC plan, so
only the policy can pick the right reason.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW, fuel in L.
"""

from __future__ import annotations

from core.types import BatterySpec, DieselSpec, StepResult


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def simulate_hour(
    t_label: str,
    u_dg: bool,
    pv_actual_kw: float,
    wind_actual_kw: float,
    load_critical_kw: float,
    load_essential_kw: float,
    load_deferrable_kw: float,
    soc_prev_kwh: float,
    battery: BatterySpec,
    diesel: DieselSpec,
) -> StepResult:
    """Resolve one hour of physics given a diesel on/off decision (`u_dg`)
    and realized renewables/load. See module docstring; steps below mirror
    the spec's STEP 1-7 exactly.
    """
    # STEP 1 — serve load from renewables first
    renew_avail = pv_actual_kw + wind_actual_kw
    total_load = load_critical_kw + load_essential_kw + load_deferrable_kw
    renew_for_load = min(renew_avail, total_load)
    remaining_load = total_load - renew_for_load
    renew_surplus = renew_avail - renew_for_load

    # STEP 2 — diesel (if on) covers remaining_load, clipped to its range
    if u_dg:
        p_dg = _clip(remaining_load, diesel.min_load_frac * diesel.rated_kw, diesel.rated_kw)
    else:
        p_dg = 0.0
    remaining_load_after_dg = max(0.0, remaining_load - p_dg)
    dg_surplus = max(0.0, p_dg - remaining_load)  # forced by diesel's min-load floor

    # STEP 3 — surplus (renewables + diesel min-load overshoot) charges the
    # battery, bounded by charge power and SOC headroom; rest is curtailed
    avail_surplus = renew_surplus + dg_surplus
    headroom_kwh = max(0.0, battery.soc_max_pct / 100.0 * battery.capacity_kwh - soc_prev_kwh)
    charge_headroom_kw = headroom_kwh / battery.eta_charge if battery.eta_charge > 0 else 0.0
    p_ch = min(avail_surplus, battery.p_charge_max_kw, charge_headroom_kw)
    curtailed_kwh = avail_surplus - p_ch

    # STEP 4 — remaining deficit (after diesel) met by battery discharge
    available_kwh = max(0.0, soc_prev_kwh - battery.soc_min_pct / 100.0 * battery.capacity_kwh)
    p_dis = min(remaining_load_after_dg, battery.p_discharge_max_kw, available_kwh * battery.eta_discharge)
    remaining_deficit = remaining_load_after_dg - p_dis

    # STEP 5 — shed by priority: deferrable first, then essential, then critical
    shed_d = min(remaining_deficit, load_deferrable_kw)
    remaining_deficit -= shed_d
    shed_e = min(remaining_deficit, load_essential_kw)
    remaining_deficit -= shed_e
    shed_c = min(remaining_deficit, load_critical_kw)
    remaining_deficit -= shed_c
    assert remaining_deficit < 1e-6, f"unresolved deficit at {t_label}: {remaining_deficit}"

    # STEP 6 — SOC update, same formula as milp.py's C3
    soc_new_kwh = soc_prev_kwh + battery.eta_charge * p_ch - p_dis / battery.eta_discharge

    # STEP 7 — fuel
    fuel_l = diesel.fuel_a_l_per_kw_h * diesel.rated_kw * int(u_dg) + diesel.fuel_b_l_per_kwh * p_dg

    # Display split of curtailment between pv_kw/wind_kw: allocate
    # curtailed_kwh proportionally to each source's share of renew_avail
    # when there was renewable surplus to curtail from; dg_surplus has no
    # display field of its own (dg_kw stays == p_dg regardless) — its
    # curtailment is just energy that went nowhere useful, visible only in
    # curtailed_kwh.
    if renew_surplus > 1e-9 and renew_avail > 1e-9:
        pv_curtailed = curtailed_kwh * (pv_actual_kw / renew_avail)
        wind_curtailed = curtailed_kwh * (wind_actual_kw / renew_avail)
    else:
        pv_curtailed = 0.0
        wind_curtailed = 0.0

    return StepResult(
        t=t_label,
        pv_kw=pv_actual_kw - pv_curtailed,
        wind_kw=wind_actual_kw - wind_curtailed,
        dg_kw=p_dg,
        dg_on=int(u_dg),
        batt_kw=p_dis - p_ch,  # positive = discharge, negative = charge
        soc_kwh=soc_new_kwh,
        soc_pct=100.0 * soc_new_kwh / battery.capacity_kwh,
        load_critical_kw=load_critical_kw,
        load_essential_kw=load_essential_kw,
        load_deferrable_kw=load_deferrable_kw,
        unserved_critical_kwh=shed_c,
        unserved_essential_kwh=shed_e,
        unserved_deferrable_kwh=shed_d,
        curtailed_kwh=curtailed_kwh,
        fuel_l=fuel_l,
        reason_code="",
        reason_text="",
    )


def simulate_hour_diesel_only(
    t_label: str,
    load_critical_kw: float,
    load_essential_kw: float,
    load_deferrable_kw: float,
    soc_prev_kwh: float,
    battery: BatterySpec,
    diesel: DieselSpec,
) -> StepResult:
    """The literal "diesel only" baseline: no renewables, no battery
    engaged at all. Diesel (always on, unless rated_kw<=0 -- BUGFIX-1,
    there is then nothing to be on) alone serves load, clipped to its
    rated range; anything beyond diesel's capacity is shed by the same
    deferrable -> essential -> critical priority as `simulate_hour`. Does
    NOT call `simulate_hour` — this is a deliberately separate, simpler
    physics path with no renewable or battery interaction whatsoever.
    """
    total_load = load_critical_kw + load_essential_kw + load_deferrable_kw
    p_dg = _clip(total_load, diesel.min_load_frac * diesel.rated_kw, diesel.rated_kw)
    remaining_deficit = max(0.0, total_load - p_dg)

    shed_d = min(remaining_deficit, load_deferrable_kw)
    remaining_deficit -= shed_d
    shed_e = min(remaining_deficit, load_essential_kw)
    remaining_deficit -= shed_e
    shed_c = min(remaining_deficit, load_critical_kw)
    remaining_deficit -= shed_c
    assert remaining_deficit < 1e-6, f"unresolved deficit at {t_label}: {remaining_deficit}"

    fuel_l = diesel.fuel_a_l_per_kw_h * diesel.rated_kw + diesel.fuel_b_l_per_kwh * p_dg

    # BUGFIX-1: a site with no diesel genset (rated_kw<=0) has nothing to
    # ever be "on" -- p_dg/fuel_l already correctly clip to 0 above, but
    # dg_on was hardcoded to 1 ("always on") regardless, which made
    # compute_kpi count a phantom dg_start for a generator that doesn't
    # exist. Khavda always has diesel.rated_kw>0, so this is unaffected.
    dg_on = 1 if diesel.rated_kw > 0 else 0

    return StepResult(
        t=t_label,
        pv_kw=0.0,
        wind_kw=0.0,
        dg_kw=p_dg,
        dg_on=dg_on,
        batt_kw=0.0,
        soc_kwh=soc_prev_kwh,
        soc_pct=100.0 * soc_prev_kwh / battery.capacity_kwh,
        load_critical_kw=load_critical_kw,
        load_essential_kw=load_essential_kw,
        load_deferrable_kw=load_deferrable_kw,
        unserved_critical_kwh=shed_c,
        unserved_essential_kwh=shed_e,
        unserved_deferrable_kwh=shed_d,
        curtailed_kwh=0.0,
        fuel_l=fuel_l,
        reason_code="",
        reason_text="",
    )
