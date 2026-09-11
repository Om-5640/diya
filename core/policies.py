"""
DIYA core/policies.py — the four dispatch policies.

Each policy takes (scenario_df, site, max_hours=None) and turns realized
weather/load rows into a list[StepResult], attaching a reason code to each
step. `run_diesel_only` and `run_rule_based` return list[StepResult]
directly.

DEVIATION NOTE: `run_mpc` and `run_perfect_foresight` return
tuple[list[StepResult], list[float]] (steps, solve_ms) instead of a bare
list[StepResult] — StepResult/RunResult carry no per-step solve-time field,
and core/kpi.py's compute_kpi explicitly takes a separate solve_ms_list
parameter for the two solver-backed policies, so the timing has to leave
the policy function through a second return value. core/runner.py accounts
for the two return shapes.

`site.economics` is expected to already carry any scenario diesel-price
multiplier applied by the caller (core/runner.py) — no policy here applies
one itself.

If `max_hours` is given, scenario_df is truncated to that many rows first
(for fast tests; scripts/precompute_runs.py always uses the full 168).

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW, fuel in L.
"""

from __future__ import annotations

import pandas as pd

from core.milp import solve_dispatch
from core.reasons import (
    reason_for_diesel_only_step,
    reason_for_planned_step,
    reason_for_reactive_step,
)
from core.simulator import simulate_hour, simulate_hour_diesel_only
from core.types import DispatchPlan, SiteConfig, StepResult

SOC_LOW_PCT_DEFAULT = 40.0
SOC_HIGH_PCT_DEFAULT = 80.0


def _maybe_truncate(scenario_df: pd.DataFrame, max_hours: int | None) -> pd.DataFrame:
    return scenario_df if max_hours is None else scenario_df.iloc[:max_hours]


def run_diesel_only(
    scenario_df: pd.DataFrame,
    site: SiteConfig,
    max_hours: int | None = None,
) -> list[StepResult]:
    """Literal diesel-only baseline: no renewables, no battery. dg_on is 1
    whenever there's any load (see simulate_hour_diesel_only)."""
    df = _maybe_truncate(scenario_df, max_hours)
    battery = site.battery
    diesel = site.diesel

    soc_kwh = battery.soc_init_pct / 100.0 * battery.capacity_kwh  # frozen, unused by the physics

    steps: list[StepResult] = []
    for row in df.itertuples():
        step = simulate_hour_diesel_only(
            t_label=row.timestamp.isoformat(),
            load_critical_kw=row.load_critical_kw,
            load_essential_kw=row.load_essential_kw,
            load_deferrable_kw=row.load_deferrable_kw,
            soc_prev_kwh=soc_kwh,
            battery=battery,
            diesel=diesel,
        )
        code, text = reason_for_diesel_only_step(step)
        step.reason_code = code
        step.reason_text = text
        steps.append(step)

    return steps


def run_rule_based(
    scenario_df: pd.DataFrame,
    site: SiteConfig,
    max_hours: int | None = None,
    soc_low_pct: float = SOC_LOW_PCT_DEFAULT,
    soc_high_pct: float = SOC_HIGH_PCT_DEFAULT,
) -> list[StepResult]:
    """Reactive SOC-threshold rule: no forecast, just battery.soc_pct
    hysteresis around [soc_low_pct, soc_high_pct]."""
    df = _maybe_truncate(scenario_df, max_hours)
    battery = site.battery
    diesel = site.diesel

    soc_kwh = battery.soc_init_pct / 100.0 * battery.capacity_kwh
    u_dg_prev = False

    steps: list[StepResult] = []
    for row in df.itertuples():
        soc_pct_prev = 100.0 * soc_kwh / battery.capacity_kwh
        if soc_pct_prev < soc_low_pct:
            u_dg = True
        elif soc_pct_prev > soc_high_pct:
            u_dg = False
        else:
            u_dg = u_dg_prev

        step = simulate_hour(
            t_label=row.timestamp.isoformat(),
            u_dg=u_dg,
            pv_actual_kw=row.pv_actual_kw,
            wind_actual_kw=row.wind_actual_kw,
            load_critical_kw=row.load_critical_kw,
            load_essential_kw=row.load_essential_kw,
            load_deferrable_kw=row.load_deferrable_kw,
            soc_prev_kwh=soc_kwh,
            battery=battery,
            diesel=diesel,
        )
        code, text = reason_for_reactive_step(u_dg, soc_pct_prev, step, soc_low_pct, soc_high_pct, diesel)
        step.reason_code = code
        step.reason_text = text
        steps.append(step)

        soc_kwh = step.soc_kwh
        u_dg_prev = u_dg

    return steps


def run_mpc(
    scenario_df: pd.DataFrame,
    site: SiteConfig,
    max_hours: int | None = None,
    time_limit_s: float = 3.0,
) -> tuple[list[StepResult], list[float]]:
    """Rolling-horizon MPC: each hour, solve a forecast-driven MILP over
    the next site.horizon_hours (truncated near the end of the scenario),
    commit only its first hour's decision, then resolve REALIZED physics
    for that hour via simulate_hour.

    NO-HINDSIGHT-LEAKAGE RULE (core/forecast.py's CRITICAL RULE, enforced
    here at the policy level): pv_fc_kw/wind_fc_kw go into solve_dispatch.
    pv_actual_kw/wind_actual_kw go into simulate_hour. Never swap these.

    SIMPLIFICATION: load is treated as perfectly known in this MVP -- only
    weather/generation (pv, wind) is genuinely uncertain. The load_*_kw
    columns serve as both "forecast" (into solve_dispatch) and "actual"
    (into simulate_hour) for that reason.
    """
    df = _maybe_truncate(scenario_df, max_hours)
    battery = site.battery
    diesel = site.diesel
    economics = site.economics

    soc_kwh = battery.soc_init_pct / 100.0 * battery.capacity_kwh
    u_dg_prev = False

    steps: list[StepResult] = []
    solve_ms_list: list[float] = []

    n = len(df)
    for t in range(n):
        h_t = min(site.horizon_hours, n - t)
        window = df.iloc[t : t + h_t]

        plan = solve_dispatch(
            pv_forecast_kw=window["pv_fc_kw"].tolist(),
            wind_forecast_kw=window["wind_fc_kw"].tolist(),
            load_critical_kw=window["load_critical_kw"].tolist(),
            load_essential_kw=window["load_essential_kw"].tolist(),
            load_deferrable_kw=window["load_deferrable_kw"].tolist(),
            soc_init_kwh=soc_kwh,
            dg_on_prev=u_dg_prev,
            battery=battery,
            diesel=diesel,
            economics=economics,
            reserve_hours=site.reserve_hours,
            k_uncertainty=site.k_uncertainty,
            time_limit_s=time_limit_s,
        )
        solve_ms_list.append(plan.solve_ms)

        u_dg_t = bool(plan.dg_on[0])
        soc_pct_at_commit = 100.0 * soc_kwh / battery.capacity_kwh

        code, text = reason_for_planned_step(
            plan,
            pv_fc_window=window["pv_fc_kw"].tolist(),
            wind_fc_window=window["wind_fc_kw"].tolist(),
            load_essential_fc_window=window["load_essential_kw"].tolist(),
            load_critical_fc_window=window["load_critical_kw"].tolist(),
            soc_pct_at_commit=soc_pct_at_commit,
            reserve_hours=site.reserve_hours,
            k_uncertainty=site.k_uncertainty,
            diesel=diesel,
            battery=battery,
        )

        row = df.iloc[t]
        step = simulate_hour(
            t_label=row["timestamp"].isoformat(),
            u_dg=u_dg_t,
            pv_actual_kw=row["pv_actual_kw"],  # REALIZED, not forecast
            wind_actual_kw=row["wind_actual_kw"],  # REALIZED, not forecast
            load_critical_kw=row["load_critical_kw"],
            load_essential_kw=row["load_essential_kw"],
            load_deferrable_kw=row["load_deferrable_kw"],
            soc_prev_kwh=soc_kwh,
            battery=battery,
            diesel=diesel,
        )
        step.reason_code = code
        step.reason_text = text
        steps.append(step)

        soc_kwh = step.soc_kwh
        u_dg_prev = u_dg_t

    return steps, solve_ms_list


def _plan_window(plan: DispatchPlan, t: int) -> DispatchPlan:
    """A view of `plan` starting at absolute index t, re-indexed to 0, so
    reason_for_planned_step's hard-coded index-[0] logic applies to step t
    of the full plan. Only the list fields matter for that function; the
    scalar fields (solve_status/solve_ms/objective_value) are carried
    through unchanged since they aren't per-step."""
    return DispatchPlan(
        horizon_hours=plan.horizon_hours - t,
        pv_use_kw=plan.pv_use_kw[t:],
        wind_use_kw=plan.wind_use_kw[t:],
        dg_kw=plan.dg_kw[t:],
        dg_on=plan.dg_on[t:],
        dg_start=plan.dg_start[t:],
        p_charge_kw=plan.p_charge_kw[t:],
        p_discharge_kw=plan.p_discharge_kw[t:],
        soc_kwh=plan.soc_kwh[t:],
        unserved_critical_kwh=plan.unserved_critical_kwh[t:],
        unserved_essential_kwh=plan.unserved_essential_kwh[t:],
        unserved_deferrable_kwh=plan.unserved_deferrable_kwh[t:],
        curtailed_kwh=plan.curtailed_kwh[t:],
        fuel_l=plan.fuel_l[t:],
        solve_status=plan.solve_status,
        solve_ms=plan.solve_ms,
        objective_value=plan.objective_value,
    )


def run_perfect_foresight(
    scenario_df: pd.DataFrame,
    site: SiteConfig,
    max_hours: int | None = None,
    time_limit_s: float = 30.0,
    gap_rel: float = 0.01,
) -> tuple[list[StepResult], list[float]]:
    """One MILP solve over the FULL horizon with forecast == actual (the
    theoretical upper bound: perfect knowledge of the future). Since
    forecast==actual exactly, steps are read DIRECTLY from the plan's
    arrays -- no simulate_hour reconciliation, they are mathematically the
    same dispatch. reserve_hours=0, k_uncertainty=0.0: perfect foresight
    needs no uncertainty buffer since nothing is uncertain to it.
    """
    df = _maybe_truncate(scenario_df, max_hours)
    battery = site.battery
    diesel = site.diesel
    economics = site.economics

    soc_init_kwh = battery.soc_init_pct / 100.0 * battery.capacity_kwh

    plan = solve_dispatch(
        pv_forecast_kw=df["pv_actual_kw"].tolist(),
        wind_forecast_kw=df["wind_actual_kw"].tolist(),
        load_critical_kw=df["load_critical_kw"].tolist(),
        load_essential_kw=df["load_essential_kw"].tolist(),
        load_deferrable_kw=df["load_deferrable_kw"].tolist(),
        soc_init_kwh=soc_init_kwh,
        dg_on_prev=False,
        battery=battery,
        diesel=diesel,
        economics=economics,
        reserve_hours=0,
        k_uncertainty=0.0,
        time_limit_s=time_limit_s,
        gap_rel=gap_rel,
    )

    n = len(df)
    pv_actual = df["pv_actual_kw"].tolist()
    wind_actual = df["wind_actual_kw"].tolist()
    load_critical = df["load_critical_kw"].tolist()
    load_essential = df["load_essential_kw"].tolist()
    load_deferrable = df["load_deferrable_kw"].tolist()
    timestamps = df["timestamp"].tolist()

    steps: list[StepResult] = []
    for t in range(n):
        soc_prev_kwh = soc_init_kwh if t == 0 else plan.soc_kwh[t - 1]
        soc_pct_at_commit = 100.0 * soc_prev_kwh / battery.capacity_kwh

        code, text = reason_for_planned_step(
            _plan_window(plan, t),
            pv_fc_window=pv_actual[t:],
            wind_fc_window=wind_actual[t:],
            load_essential_fc_window=load_essential[t:],
            load_critical_fc_window=load_critical[t:],
            soc_pct_at_commit=soc_pct_at_commit,
            reserve_hours=0,
            k_uncertainty=0.0,
            diesel=diesel,
            battery=battery,
        )

        steps.append(
            StepResult(
                t=timestamps[t].isoformat(),
                pv_kw=plan.pv_use_kw[t],
                wind_kw=plan.wind_use_kw[t],
                dg_kw=plan.dg_kw[t],
                dg_on=plan.dg_on[t],
                batt_kw=plan.p_discharge_kw[t] - plan.p_charge_kw[t],
                soc_kwh=plan.soc_kwh[t],
                soc_pct=100.0 * plan.soc_kwh[t] / battery.capacity_kwh,
                load_critical_kw=load_critical[t],
                load_essential_kw=load_essential[t],
                load_deferrable_kw=load_deferrable[t],
                unserved_critical_kwh=plan.unserved_critical_kwh[t],
                unserved_essential_kwh=plan.unserved_essential_kwh[t],
                unserved_deferrable_kwh=plan.unserved_deferrable_kwh[t],
                curtailed_kwh=plan.curtailed_kwh[t],
                fuel_l=plan.fuel_l[t],
                reason_code=code,
                reason_text=text,
            )
        )

    return steps, [plan.solve_ms]
