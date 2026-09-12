"""
DIYA core/reasons.py — reason-code assignment for planned (MPC) and
reactive (rule-based) dispatch decisions.

NOTE ON SIGNATURES: `reason_for_planned_step`'s R4_RESERVE_HOLD check and
`reason_for_reactive_step`'s R5_MINLOAD check both need battery/diesel specs
to recompute their formulas (soc_min_pct/capacity_kwh, min_load_frac*rated_kw
respectively). Both are added here as explicit parameters even though the
originating spec text didn't list them in the signature — the formulas are
literally uncomputable without them.

DELIBERATE DEVIATION FROM "ONE CODE = ONE FIXED TEXT": in
`reason_for_reactive_step`, the R3_CHEAPER_DIESEL fallback returns a
DIFFERENT text than REASON_CODES["R3_CHEAPER_DIESEL"] (the MILP-aware
version used by `reason_for_planned_step`). This is intentional: the code
categorizes "diesel is on for cost reasons", but the text should reflect
what kind of reasoning actually produced that decision — a naive
SOC-threshold rule versus an informed MILP tradeoff — so the demo can
visibly contrast the two for the same hour. Every other reason code keeps
its exact REASON_CODES text unchanged.
"""

from __future__ import annotations

from core.types import BatterySpec, DieselSpec, DispatchPlan, SiteConfig, StepResult, reason_text_for


def _reason(code: str, site: SiteConfig) -> tuple[str, str]:
    return code, reason_text_for(code, site)


def _forward_window(seq, start: int = 1, end: int = 7) -> list[float]:
    """seq[start:end], falling back to seq[:1] if that slice is empty (so
    there is always at least 1 value, per the spec's guard) — and to an
    empty list only if `seq` itself has nothing at all."""
    window = list(seq)[start:end]
    if not window:
        window = list(seq)[:1]
    return window


def _safe_mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _reserve_floor_kwh(
    load_critical_window: list[float],
    reserve_hours: int,
    k_uncertainty: float,
    battery: BatterySpec,
) -> float:
    """Same reserve-floor formula as milp.py's C9, evaluated for t=0."""
    H = len(load_critical_window)
    soc_min_kwh = battery.soc_min_pct / 100.0 * battery.capacity_kwh
    soc_max_kwh = battery.soc_max_pct / 100.0 * battery.capacity_kwh

    window_start = 1
    window_end = min(1 + reserve_hours, H)
    if H == 0:
        mean_forward_critical = 0.0
    elif window_start >= H:
        mean_forward_critical = load_critical_window[H - 1]
    else:
        window_vals = list(load_critical_window[window_start:window_end])
        if not window_vals:
            window_vals = [load_critical_window[H - 1]]
        mean_forward_critical = sum(window_vals) / len(window_vals)

    reserve_floor = soc_min_kwh + reserve_hours * k_uncertainty * mean_forward_critical
    return min(reserve_floor, soc_max_kwh)


def reason_for_planned_step(
    plan: DispatchPlan,
    pv_fc_window,
    wind_fc_window,
    load_essential_fc_window,
    load_critical_fc_window,
    soc_pct_at_commit: float,
    reserve_hours: int,
    k_uncertainty: float,
    diesel: DieselSpec,
    battery: BatterySpec,
    site: SiteConfig,
) -> tuple[str, str]:
    """Reason for the committed step (index 0) of an MPC-produced
    DispatchPlan, given the forecast windows used to produce it. Checks are
    evaluated in a fixed priority order; the first match wins. `site` is
    used only for reason_text_for's narrative labels (BUGFIX-1) -- no
    other reasoning here depends on it.
    """
    if plan.unserved_critical_kwh[0] > 1e-6:
        return _reason("R8_CRITICAL_DEFICIT", site)

    if plan.dg_on[0] == 1 and soc_pct_at_commit > 60:
        renew_mean = _safe_mean(_forward_window(pv_fc_window) + _forward_window(wind_fc_window))
        load_mean = _safe_mean(_forward_window(load_essential_fc_window) + _forward_window(load_critical_fc_window))
        if renew_mean < 0.6 * load_mean:
            return _reason("R1_PREPOSITION", site)

    if reserve_hours > 0:
        reserve_floor = _reserve_floor_kwh(list(load_critical_fc_window), reserve_hours, k_uncertainty, battery)
        if abs(plan.soc_kwh[0] - reserve_floor) <= 0.5:
            return _reason("R4_RESERVE_HOLD", site)

    if plan.p_charge_kw[0] > 1e-6 and plan.dg_on[0] == 0:
        return _reason("R2_SOLAR_SURPLUS", site)

    if plan.dg_on[0] == 1 and abs(plan.dg_kw[0] - diesel.min_load_frac * diesel.rated_kw) < 1e-3:
        return _reason("R5_MINLOAD", site)

    if plan.dg_on[0] == 1:
        return _reason("R3_CHEAPER_DIESEL", site)

    if plan.dg_on[0] == 0 and plan.p_discharge_kw[0] > 1e-6:
        return _reason("R6_AVOID_START", site)

    if plan.unserved_deferrable_kwh[0] > 1e-6:
        return _reason("R7_SHED_DEFERRABLE", site)

    return _reason("R0_NOMINAL", site)


def reason_for_reactive_step(
    u_dg: bool,
    soc_pct_prev: float,
    step: StepResult,
    soc_low_pct: float,
    soc_high_pct: float,
    diesel: DieselSpec,
    site: SiteConfig,
) -> tuple[str, str]:
    """Reason for a rule_based (no-forecast) step. Deliberately excludes
    R1_PREPOSITION and R4_RESERVE_HOLD — rule_based has no forecast and
    cannot preposition or reason about a forward reserve. See module
    docstring for why R3_CHEAPER_DIESEL's text differs from the MILP-aware
    version here.
    """
    if step.unserved_critical_kwh > 1e-6:
        return _reason("R8_CRITICAL_DEFICIT", site)

    if step.batt_kw < -1e-6 and not u_dg:
        return _reason("R2_SOLAR_SURPLUS", site)

    if u_dg and abs(step.dg_kw - diesel.min_load_frac * diesel.rated_kw) < 1e-3:
        return _reason("R5_MINLOAD", site)

    if step.unserved_deferrable_kwh > 1e-6:
        return _reason("R7_SHED_DEFERRABLE", site)

    if not u_dg and step.batt_kw > 1e-6:
        return _reason("R6_AVOID_START", site)

    if u_dg:
        return (
            "R3_CHEAPER_DIESEL",
            f"Diesel running — battery state of charge fell below the {soc_low_pct:.0f}% "
            f"start threshold.",
        )

    return _reason("R0_NOMINAL", site)


def reason_for_diesel_only_step(step: StepResult, site: SiteConfig) -> tuple[str, str]:
    """Reason for a run_diesel_only step: always R9_DIESEL_ONLY, unless
    diesel's own rated capacity couldn't cover critical load."""
    if step.unserved_critical_kwh > 1e-6:
        return _reason("R8_CRITICAL_DEFICIT", site)
    return _reason("R9_DIESEL_ONLY", site)
