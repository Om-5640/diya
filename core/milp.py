"""
DIYA core/milp.py — single-horizon MILP dispatch optimizer (PuLP/CBC).

Solves one dispatch horizon given FORECAST renewable generation and load,
plus the battery's current state of charge. This solver must NEVER receive
the realized series directly — see core/forecast.py's CRITICAL RULE: only
forecast output may reach the optimizer, with the sole, explicitly-named
exception of the perfect_foresight policy (a later phase), which may
legitimately pass realized values in as "forecast".

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW, fuel in L.
"""

from __future__ import annotations

import time

import pulp

from core.types import BatterySpec, DieselSpec, DispatchPlan, EconomicsSpec

# The specified objective has no term pricing curtailment, which leaves the
# charge-vs-curtail split degenerate whenever renewables exceed load with no
# diesel running (any split costs exactly 0). This epsilon is a tie-breaker
# only: many orders of magnitude below any real fuel/VOLL/start cost, so it
# never changes a real dispatch tradeoff, but it consistently prefers storing
# free surplus energy over wasting it.
EPSILON_CURTAIL_PENALTY_INR_PER_KWH = 1e-6

_STATUS_MAP = {
    pulp.LpStatusOptimal: "Optimal",
    pulp.LpStatusInfeasible: "Infeasible",
    pulp.LpStatusUnbounded: "Error",
    pulp.LpStatusUndefined: "Timeout",
    pulp.LpStatusNotSolved: "Error",
}


def solve_dispatch(
    pv_forecast_kw: list[float],
    wind_forecast_kw: list[float],
    load_critical_kw: list[float],
    load_essential_kw: list[float],
    load_deferrable_kw: list[float],
    soc_init_kwh: float,
    dg_on_prev: bool,
    battery: BatterySpec,
    diesel: DieselSpec,
    economics: EconomicsSpec,
    reserve_hours: int,
    k_uncertainty: float,
    time_limit_s: float = 5.0,
    gap_rel: float = 0.005,
) -> DispatchPlan:
    """Solve one dispatch horizon and return a DispatchPlan.

    `economics.diesel_price_inr_per_l` is used as-is — any scenario
    diesel_price_multiplier must already be applied by the caller before
    this function is invoked; this function applies no multipliers itself.
    """
    lengths = {
        len(pv_forecast_kw),
        len(wind_forecast_kw),
        len(load_critical_kw),
        len(load_essential_kw),
        len(load_deferrable_kw),
    }
    if len(lengths) != 1:
        raise ValueError("pv/wind/load forecast lists must all be the same length")
    horizon_hours = lengths.pop()
    H = horizon_hours

    soc_min_kwh = battery.soc_min_pct / 100.0 * battery.capacity_kwh
    soc_max_kwh = battery.soc_max_pct / 100.0 * battery.capacity_kwh

    prob = pulp.LpProblem("diya_dispatch", pulp.LpMinimize)

    pv_use = [pulp.LpVariable(f"pv_use_{t}", lowBound=0) for t in range(H)]
    wind_use = [pulp.LpVariable(f"wind_use_{t}", lowBound=0) for t in range(H)]
    p_dg = [pulp.LpVariable(f"p_dg_{t}", lowBound=0) for t in range(H)]
    u_dg = [pulp.LpVariable(f"u_dg_{t}", cat="Binary") for t in range(H)]
    y_start = [pulp.LpVariable(f"y_start_{t}", cat="Binary") for t in range(H)]
    p_ch = [pulp.LpVariable(f"p_ch_{t}", lowBound=0) for t in range(H)]
    p_dis = [pulp.LpVariable(f"p_dis_{t}", lowBound=0) for t in range(H)]
    soc = [pulp.LpVariable(f"soc_{t}", lowBound=0) for t in range(H)]
    s_c = [pulp.LpVariable(f"s_c_{t}", lowBound=0) for t in range(H)]
    s_e = [pulp.LpVariable(f"s_e_{t}", lowBound=0) for t in range(H)]
    s_d = [pulp.LpVariable(f"s_d_{t}", lowBound=0) for t in range(H)]

    for t in range(H):
        # C1 energy balance
        prob += (
            pv_use[t] + wind_use[t] + p_dg[t] + p_dis[t]
            == (load_critical_kw[t] - s_c[t])
            + (load_essential_kw[t] - s_e[t])
            + (load_deferrable_kw[t] - s_d[t])
            + p_ch[t]
        ), f"C1_balance_{t}"

        # C2 renewable caps (curtailment computed post-solve, no variable)
        prob += pv_use[t] <= pv_forecast_kw[t], f"C2_pv_cap_{t}"
        prob += wind_use[t] <= wind_forecast_kw[t], f"C2_wind_cap_{t}"

        # C3 SOC dynamics
        soc_prev = soc_init_kwh if t == 0 else soc[t - 1]
        prob += (
            soc[t] == soc_prev + battery.eta_charge * p_ch[t] - (1.0 / battery.eta_discharge) * p_dis[t]
        ), f"C3_soc_dynamics_{t}"

        # C4 SOC bounds
        prob += soc[t] >= soc_min_kwh, f"C4_soc_min_{t}"
        prob += soc[t] <= soc_max_kwh, f"C4_soc_max_{t}"

        # C5 battery power limits
        prob += p_ch[t] <= battery.p_charge_max_kw, f"C5_charge_max_{t}"
        prob += p_dis[t] <= battery.p_discharge_max_kw, f"C5_discharge_max_{t}"

        # C6 diesel range
        prob += p_dg[t] >= diesel.min_load_frac * diesel.rated_kw * u_dg[t], f"C6_dg_min_{t}"
        prob += p_dg[t] <= diesel.rated_kw * u_dg[t], f"C6_dg_max_{t}"

        # C7 starts
        prev_on = (1 if dg_on_prev else 0) if t == 0 else u_dg[t - 1]
        prob += y_start[t] >= u_dg[t] - prev_on, f"C7_start_lb_{t}"
        prob += y_start[t] <= 1, f"C7_start_ub_{t}"
        prob += y_start[t] >= 0, f"C7_start_nonneg_{t}"

        # C8 shed caps
        prob += s_c[t] <= load_critical_kw[t], f"C8_shed_c_{t}"
        prob += s_e[t] <= load_essential_kw[t], f"C8_shed_e_{t}"
        prob += s_d[t] <= load_deferrable_kw[t], f"C8_shed_d_{t}"

        # C9 reserve — soc[t] must stay above soc_min plus a forward-looking
        # reserve for critical load, capped so it never conflicts with C4's
        # soc_max upper bound. Skipped entirely when reserve_hours <= 0.
        if reserve_hours > 0:
            window_start = t + 1
            window_end = min(t + 1 + reserve_hours, H)
            if window_start >= H:
                window_vals = [load_critical_kw[H - 1]]
            else:
                window_vals = load_critical_kw[window_start:window_end]
                if not window_vals:
                    window_vals = [load_critical_kw[H - 1]]
            mean_forward_critical = sum(window_vals) / len(window_vals)
            reserve_floor = soc_min_kwh + reserve_hours * k_uncertainty * mean_forward_critical
            reserve_floor = min(reserve_floor, soc_max_kwh)
            prob += soc[t] >= reserve_floor, f"C9_reserve_{t}"

    obj_terms = []
    for t in range(H):
        fuel_lt = diesel.fuel_a_l_per_kw_h * diesel.rated_kw * u_dg[t] + diesel.fuel_b_l_per_kwh * p_dg[t]
        obj_terms.append(economics.diesel_price_inr_per_l * fuel_lt)
        obj_terms.append(economics.co2_price_inr_per_kg * diesel.co2_kg_per_l * fuel_lt)
        obj_terms.append(diesel.start_cost_inr * y_start[t])
        obj_terms.append(economics.voll_critical_inr_per_kwh * s_c[t])
        obj_terms.append(economics.voll_essential_inr_per_kwh * s_e[t])
        obj_terms.append(economics.voll_deferrable_inr_per_kwh * s_d[t])
        curt_t = (pv_forecast_kw[t] + wind_forecast_kw[t]) - pv_use[t] - wind_use[t]
        obj_terms.append(EPSILON_CURTAIL_PENALTY_INR_PER_KWH * curt_t)
    prob += pulp.lpSum(obj_terms)

    status_str, solve_ms = _solve_with_retry(prob, time_limit_s, gap_rel)

    def _val(var: pulp.LpVariable) -> float:
        v = var.value()
        return float(v) if v is not None else 0.0

    has_solution = any(v.value() is not None for v in soc)
    if status_str != "Optimal" and not has_solution:
        raise RuntimeError(
            f"MILP solve failed with status={status_str} and no solution values were "
            "returned by the solver -- this indicates a formulation bug, not an "
            "expected outcome, and must be fixed rather than papered over."
        )

    dg_on_arr = [int(round(_val(u_dg[t]))) for t in range(H)]
    dg_start_arr = [int(round(_val(y_start[t]))) for t in range(H)]
    fuel_l_arr = [
        diesel.fuel_a_l_per_kw_h * diesel.rated_kw * dg_on_arr[t] + diesel.fuel_b_l_per_kwh * _val(p_dg[t])
        for t in range(H)
    ]
    curtailed_arr = [
        max(0.0, (pv_forecast_kw[t] + wind_forecast_kw[t]) - _val(pv_use[t]) - _val(wind_use[t]))
        for t in range(H)
    ]

    obj_value = pulp.value(prob.objective)

    return DispatchPlan(
        horizon_hours=H,
        pv_use_kw=[_val(pv_use[t]) for t in range(H)],
        wind_use_kw=[_val(wind_use[t]) for t in range(H)],
        dg_kw=[_val(p_dg[t]) for t in range(H)],
        dg_on=dg_on_arr,
        dg_start=dg_start_arr,
        p_charge_kw=[_val(p_ch[t]) for t in range(H)],
        p_discharge_kw=[_val(p_dis[t]) for t in range(H)],
        soc_kwh=[_val(soc[t]) for t in range(H)],
        unserved_critical_kwh=[_val(s_c[t]) for t in range(H)],
        unserved_essential_kwh=[_val(s_e[t]) for t in range(H)],
        unserved_deferrable_kwh=[_val(s_d[t]) for t in range(H)],
        curtailed_kwh=curtailed_arr,
        fuel_l=fuel_l_arr,
        solve_status=status_str,
        solve_ms=solve_ms,
        objective_value=float(obj_value) if obj_value is not None else 0.0,
    )


def _solve_with_retry(prob: pulp.LpProblem, time_limit_s: float, gap_rel: float) -> tuple[str, float]:
    start = time.perf_counter()
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit_s, gapRel=gap_rel))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    status_str = _STATUS_MAP.get(prob.status, "Error")

    if status_str != "Optimal":
        start2 = time.perf_counter()
        prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit_s * 3, gapRel=gap_rel))
        elapsed_ms += (time.perf_counter() - start2) * 1000.0
        status_str = _STATUS_MAP.get(prob.status, "Error")

    return status_str, elapsed_ms
