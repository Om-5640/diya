"""
DIYA core/milp.py — single-horizon MILP dispatch optimizer (PuLP, HiGHS
backend with a PULP_CBC_CMD fallback).

Solves one dispatch horizon given FORECAST renewable generation and load,
plus the battery's current state of charge. This solver must NEVER receive
the realized series directly — see core/forecast.py's CRITICAL RULE: only
forecast output may reach the optimizer, with the sole, explicitly-named
exception of the perfect_foresight policy (a later phase), which may
legitimately pass realized values in as "forecast".

SOLVER BACKEND (Phase B of DIYA v2): PuLP's native in-process `HiGHS` class
(backed by the `highspy` package, no subprocess spawned per solve) is used
when available -- this eliminates the ~60-65s cost of PULP_CBC_CMD shelling
out to cbc.exe once per hourly solve across a 168-hour rolling MPC run.
PULP_CBC_CMD remains a documented fallback if highspy isn't installed/
importable on a given machine, logged loudly so a missing solver is never
silent. The formulation below (every constraint, the objective, the
curtailment tie-breaker from Phase 2) is completely unchanged by this --
only which solver object gets handed to `prob.solve()` changed.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW, fuel in L.
"""

from __future__ import annotations

import logging
import time

import pulp

from core.types import BatterySpec, DieselSpec, DispatchPlan, EconomicsSpec

logger = logging.getLogger(__name__)

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


# Empirically measured (scripts/benchmark_solver.py + ad-hoc benchmarking
# during Phase B), NOT a guess: HiGHS's in-process branch-and-bound is
# dramatically faster than CBC's on ONE large MIP (perfect_foresight's
# single 168-hour solve, ~336 binaries: ~30.1s CBC -> ~4.3s HiGHS, a ~7x
# win) but is consistently SLOWER than CBC on MANY small, highly-binary
# rolling-horizon MIPs of the shape run_mpc and solve_live actually use
# (24h: ~198ms CBC vs ~686ms mean HiGHS; 72h single-shot: 1.73s CBC vs
# 2.91s HiGHS). This is a known characteristic of the two solvers' B&B
# implementations on unit-commitment-style problems, not a bug: verified
# it isn't the Phase 2 curtailment epsilon (zeroing it made no difference)
# and isn't Highs() construction overhead (~0.27ms, negligible). So HiGHS
# is used only for horizons at/above this threshold; below it, CBC's
# per-call overhead (even with its subprocess spawn) is empirically lower
# than HiGHS's per-call B&B cost for problems this small. The threshold
# sits between the codebase's two observed regimes (72h always favors CBC,
# 168h always favors HiGHS) with no need for finer calibration since those
# are the only two horizon lengths anything in this codebase actually uses
# for a single big solve.
HIGHS_MIN_HORIZON_HOURS = 100


def _select_solver_backend() -> str:
    """Determined ONCE at module import time, not per-solve: whether HiGHS
    is available on this machine at all. Tries PuLP's native in-process
    HiGHS class (requires the `highspy` package); falls back to
    PULP_CBC_CMD everywhere -- loudly, via both a WARNING log and a printed
    line, since a silently-missing solver must never happen. When HiGHS IS
    available, per-call routing (see _build_solver) still picks CBC for
    horizons below HIGHS_MIN_HORIZON_HOURS -- see the benchmark comment
    above for why that's the empirically faster choice, not an oversight.
    """
    try:
        probe = pulp.HiGHS(msg=False)
        if probe.available():
            msg = (
                "core.milp: HiGHS (in-process, via highspy) is available. Routing: horizons "
                f">= {HIGHS_MIN_HORIZON_HOURS}h use HiGHS (timeLimit/gapRel -> HiGHS's own "
                "'timeLimit'/'gapRel', which it maps internally to mip_rel_gap); horizons below "
                "that use PULP_CBC_CMD (timeLimit/gapRel as before) -- empirically faster for "
                "the many-small-solves rolling-MPC/live-solve shape, see core/milp.py's comment."
            )
            print(msg)
            logger.info(msg)
            return "HiGHS"
    except Exception:
        logger.warning("core.milp: HiGHS solver probe failed, falling back to PULP_CBC_CMD everywhere.", exc_info=True)

    msg = (
        "core.milp: solver backend = PULP_CBC_CMD everywhere (subprocess-per-solve fallback) -- "
        "HiGHS/highspy was not available. This is materially slower for the large single-shot "
        "perfect_foresight workload; install highspy to enable HiGHS routing for it."
    )
    print(msg)
    logger.warning(msg)
    return "PULP_CBC_CMD"


SOLVER_BACKEND = _select_solver_backend()


def _build_solver(time_limit_s: float, gap_rel: float, horizon_hours: int) -> pulp.LpSolver:
    use_highs = SOLVER_BACKEND == "HiGHS" and horizon_hours >= HIGHS_MIN_HORIZON_HOURS
    if use_highs:
        return pulp.HiGHS(msg=False, timeLimit=time_limit_s, gapRel=gap_rel)
    return pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit_s, gapRel=gap_rel)


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

    # A site with no diesel genset (rated_kw <= 0, e.g. a new renewables+
    # battery-only site created via /api/sites) must never be modeled as a
    # free binary that the solver happens to leave at 0 -- that's degenerate
    # and, worse, gives the solver no reason to prefer 0 over 1 whenever
    # rated_kw is 0 (any u_dg/p_dg combination costs the same nothing). Fix
    # these variables to 0 via bounds instead, so there is no diesel binary
    # for the solver to branch on at all. C6/C7 below still add their normal
    # constraints against these fixed-at-0 variables; they just become
    # trivially satisfied rather than doing the fixing themselves.
    diesel_disabled = diesel.rated_kw <= 0

    pv_use = [pulp.LpVariable(f"pv_use_{t}", lowBound=0) for t in range(H)]
    wind_use = [pulp.LpVariable(f"wind_use_{t}", lowBound=0) for t in range(H)]
    if diesel_disabled:
        p_dg = [pulp.LpVariable(f"p_dg_{t}", lowBound=0, upBound=0) for t in range(H)]
        u_dg = [pulp.LpVariable(f"u_dg_{t}", lowBound=0, upBound=0, cat="Integer") for t in range(H)]
        y_start = [pulp.LpVariable(f"y_start_{t}", lowBound=0, upBound=0, cat="Integer") for t in range(H)]
    else:
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
        #
        # BUGFIX-1: also skipped when diesel is disabled (rated_kw<=0). This
        # reserve exists to protect against a delayed/avoided diesel start
        # (R4_RESERVE_HOLD's own reason text is literally about that
        # tradeoff) -- it assumes diesel is available as an eventual
        # backstop that will draw on/justify the held-back energy. With no
        # diesel at all, there is nothing to wait for: withholding battery
        # capacity serves no protective purpose and only forces needless
        # unserved critical load once SOC reaches the floor, confirmed via
        # a hand-verified toy instance (tests/test_milp.py) that shows
        # reserve_hours=3 leaving load unserved with headroom still
        # available, while reserve_hours=0 on the identical instance serves
        # 100% of load down to soc_min. Khavda always has diesel.rated_kw>0,
        # so this condition never applies to it -- zero behavior change.
        if reserve_hours > 0 and not diesel_disabled:
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

    status_str, solve_ms = _solve_with_retry(prob, time_limit_s, gap_rel, H)

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


def _solve_with_retry(prob: pulp.LpProblem, time_limit_s: float, gap_rel: float, horizon_hours: int) -> tuple[str, float]:
    start = time.perf_counter()
    prob.solve(_build_solver(time_limit_s, gap_rel, horizon_hours))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    status_str = _STATUS_MAP.get(prob.status, "Error")

    if status_str != "Optimal":
        start2 = time.perf_counter()
        prob.solve(_build_solver(time_limit_s * 3, gap_rel, horizon_hours))
        elapsed_ms += (time.perf_counter() - start2) * 1000.0
        status_str = _STATUS_MAP.get(prob.status, "Error")

    return status_str, elapsed_ms
