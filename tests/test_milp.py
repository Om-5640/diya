"""
DIYA tests/test_milp.py — Phase 2 MILP dispatch tests.

Written BEFORE core/milp.py exists (TDD). Four hand-verified toy instances
(A-D), cross-instance invariants checked by recomputing the energy balance
from returned arrays (never trusting the solver blindly), and behavioural /
sensitivity checks on diesel price, reserve_hours, and k_uncertainty.

All toy instances use battery.eta_charge == battery.eta_discharge == 1.0 to
keep the hand-verified arithmetic exact, and reserve_hours=0 (C9 disabled)
unless a test says otherwise.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.milp import solve_dispatch
from core.types import BatterySpec, DieselSpec, EconomicsSpec

TEST_TIME_LIMIT_S = 2.0


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _battery(**overrides) -> BatterySpec:
    defaults = dict(
        capacity_kwh=50.0,
        soc_min_pct=10.0,
        soc_max_pct=95.0,
        p_charge_max_kw=50.0,
        p_discharge_max_kw=50.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_init_pct=50.0,
    )
    defaults.update(overrides)
    return BatterySpec(**defaults)


def _diesel(**overrides) -> DieselSpec:
    defaults = dict(
        rated_kw=10.0,
        min_load_frac=0.3,
        fuel_a_l_per_kw_h=0.08,
        fuel_b_l_per_kwh=0.25,
        start_cost_inr=150.0,
        co2_kg_per_l=2.68,
    )
    defaults.update(overrides)
    return DieselSpec(**defaults)


def _economics(**overrides) -> EconomicsSpec:
    defaults = dict(
        diesel_price_inr_per_l=92.5,
        diesel_price_source="test",
        co2_price_inr_per_kg=2.0,
        voll_critical_inr_per_kwh=500.0,
        voll_essential_inr_per_kwh=60.0,
        voll_deferrable_inr_per_kwh=12.0,
    )
    defaults.update(overrides)
    return EconomicsSpec(**defaults)


def _solve(args: dict, **overrides):
    kwargs = dict(
        pv_forecast_kw=args["pv"],
        wind_forecast_kw=args["wind"],
        load_critical_kw=args["critical"],
        load_essential_kw=args["essential"],
        load_deferrable_kw=args["deferrable"],
        soc_init_kwh=args["soc_init_kwh"],
        dg_on_prev=args.get("dg_on_prev", False),
        battery=args["battery"],
        diesel=args["diesel"],
        economics=args["economics"],
        reserve_hours=args.get("reserve_hours", 0),
        k_uncertainty=args.get("k_uncertainty", 1.0),
        time_limit_s=TEST_TIME_LIMIT_S,
    )
    kwargs.update(overrides)
    return solve_dispatch(**kwargs)


# ---------------------------------------------------------------------------
# Toy instances
# ---------------------------------------------------------------------------


def _toy_a() -> dict:
    """Renewables cover everything; pv=20 >> load=5, no diesel needed."""
    H = 6
    battery = _battery(
        capacity_kwh=50.0,
        soc_min_pct=10.0,
        soc_max_pct=95.0,
        p_charge_max_kw=50.0,
        p_discharge_max_kw=50.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_init_pct=50.0,
    )
    return dict(
        pv=[20.0] * H,
        wind=[0.0] * H,
        critical=[3.0] * H,
        essential=[2.0] * H,
        deferrable=[0.0] * H,
        battery=battery,
        diesel=_diesel(rated_kw=10.0, min_load_frac=0.3),
        economics=_economics(),
        soc_init_kwh=25.0,
        dg_on_prev=False,
    )


def _toy_b() -> dict:
    """Diesel-only: battery frozen (soc bounds pinned, charge/discharge=0),
    load=5kW is within diesel's [3,10]kW range, must be met exactly."""
    H = 6
    battery = _battery(
        capacity_kwh=10.0,
        soc_min_pct=50.0,
        soc_max_pct=50.0,
        p_charge_max_kw=0.0,
        p_discharge_max_kw=0.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_init_pct=50.0,
    )
    return dict(
        pv=[0.0] * H,
        wind=[0.0] * H,
        critical=[5.0] * H,
        essential=[0.0] * H,
        deferrable=[0.0] * H,
        battery=battery,
        diesel=_diesel(rated_kw=10.0, min_load_frac=0.3, fuel_a_l_per_kw_h=0.08, fuel_b_l_per_kwh=0.25, start_cost_inr=150.0, co2_kg_per_l=2.68),
        economics=_economics(diesel_price_inr_per_l=92.5, co2_price_inr_per_kg=2.0),
        soc_init_kwh=5.0,
        dg_on_prev=False,
    )


def _toy_c() -> dict:
    """Diesel capacity exceeded (H=1): load=15kW > diesel rated=10kW,
    battery frozen at 0kWh usable -> honest shortfall must appear."""
    battery = _battery(
        capacity_kwh=10.0,
        soc_min_pct=0.0,
        soc_max_pct=0.0,
        p_charge_max_kw=0.0,
        p_discharge_max_kw=0.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_init_pct=0.0,
    )
    return dict(
        pv=[0.0],
        wind=[0.0],
        critical=[15.0],
        essential=[0.0],
        deferrable=[0.0],
        battery=battery,
        diesel=_diesel(rated_kw=10.0, min_load_frac=0.3),
        economics=_economics(),
        soc_init_kwh=0.0,
        dg_on_prev=False,
    )


def _toy_d() -> dict:
    """Battery absorbs solar surplus: pv=8, load=5, surplus=3kW/h charges
    the battery, no diesel needed."""
    H = 6
    battery = _battery(
        capacity_kwh=50.0,
        soc_min_pct=10.0,
        soc_max_pct=95.0,
        p_charge_max_kw=10.0,
        p_discharge_max_kw=10.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_init_pct=20.0,
    )
    return dict(
        pv=[8.0] * H,
        wind=[0.0] * H,
        critical=[2.0] * H,
        essential=[3.0] * H,
        deferrable=[0.0] * H,
        battery=battery,
        diesel=_diesel(rated_kw=10.0, min_load_frac=0.3),
        economics=_economics(),
        soc_init_kwh=10.0,
        dg_on_prev=False,
    )


def _random_24h_instance(seed: int = 123) -> dict:
    """Seeded, all-four-assets-active 24h instance for invariant checks
    (no hand-verified exact values — just structural consistency)."""
    rng = np.random.default_rng(seed)
    H = 24
    hours = np.arange(H)

    pv_shape = np.clip(15.0 * np.sin(np.pi * (hours - 6) / 12.0), 0.0, None)
    pv_shape = np.where((hours >= 6) & (hours <= 18), pv_shape, 0.0)
    pv = np.clip(pv_shape + rng.normal(0, 0.5, H), 0.0, None).tolist()

    wind = rng.uniform(0.0, 5.0, H).tolist()

    critical = np.clip(3.0 + rng.normal(0, 0.2, H), 1.0, None).tolist()
    essential_shape = np.clip(6.0 * np.sin(np.pi * (hours - 14) / 12.0), 0.0, None)
    essential = np.clip(8.0 + essential_shape + rng.normal(0, 0.5, H), 0.0, None).tolist()
    deferrable = [0.0] * H

    battery = _battery(
        capacity_kwh=50.0,
        soc_min_pct=10.0,
        soc_max_pct=95.0,
        p_charge_max_kw=20.0,
        p_discharge_max_kw=20.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_init_pct=50.0,
    )
    return dict(
        pv=pv,
        wind=wind,
        critical=critical,
        essential=essential,
        deferrable=deferrable,
        battery=battery,
        diesel=_diesel(rated_kw=15.0, min_load_frac=0.3),
        economics=_economics(),
        soc_init_kwh=25.0,
        dg_on_prev=False,
    )


# ---------------------------------------------------------------------------
# Toy instance tests (hand-verified expected values)
# ---------------------------------------------------------------------------


def test_toy_a_renewables_cover_everything():
    args = _toy_a()
    plan = _solve(args)

    assert plan.solve_status == "Optimal"
    assert plan.dg_on == [0] * 6
    assert sum(plan.fuel_l) == pytest.approx(0.0, abs=1e-9)
    assert sum(plan.unserved_critical_kwh) == pytest.approx(0.0, abs=1e-9)
    assert sum(plan.unserved_essential_kwh) == pytest.approx(0.0, abs=1e-9)
    # pv covers load (5kW) plus either curtailment or battery charging for
    # the remaining 15kW/h surplus
    for t in range(6):
        assert plan.pv_use_kw[t] + plan.curtailed_kwh[t] == pytest.approx(20.0, rel=1e-4)
    assert plan.soc_kwh[5] > args["soc_init_kwh"]


def test_toy_b_diesel_only_serves_load_exactly():
    args = _toy_b()
    plan = _solve(args)

    assert plan.solve_status == "Optimal"
    assert plan.dg_on == [1] * 6
    assert plan.dg_kw == pytest.approx([5.0] * 6, rel=1e-4)
    assert plan.unserved_critical_kwh == pytest.approx([0.0] * 6, abs=1e-6)
    assert plan.fuel_l == pytest.approx([2.05] * 6, rel=1e-4)
    assert sum(plan.fuel_l) == pytest.approx(12.3, rel=1e-4)
    assert plan.dg_start == [1, 0, 0, 0, 0, 0]

    expected_obj = 92.5 * 12.3 + 2.0 * 2.68 * 12.3 + 150 * 1
    assert plan.objective_value == pytest.approx(expected_obj, rel=1e-3)


def test_toy_c_diesel_capacity_exceeded_honest_shortfall():
    args = _toy_c()
    plan = _solve(args)

    assert plan.solve_status == "Optimal"
    assert plan.dg_on == [1]
    assert plan.dg_kw == pytest.approx([10.0], rel=1e-4)
    assert plan.unserved_critical_kwh == pytest.approx([5.0], rel=1e-4)
    assert plan.fuel_l[0] == pytest.approx(3.3, rel=1e-4)
    assert plan.unserved_critical_kwh[0] > 0


def test_toy_d_battery_absorbs_solar_surplus():
    args = _toy_d()
    plan = _solve(args)

    assert plan.solve_status == "Optimal"
    assert plan.dg_on == [0] * 6
    assert sum(plan.fuel_l) == pytest.approx(0.0, abs=1e-9)
    assert sum(plan.unserved_critical_kwh) == pytest.approx(0.0, abs=1e-9)
    assert sum(plan.unserved_essential_kwh) == pytest.approx(0.0, abs=1e-9)
    assert plan.p_charge_kw == pytest.approx([3.0] * 6, rel=1e-4)
    assert plan.soc_kwh[5] == pytest.approx(28.0, rel=1e-4)


# ---------------------------------------------------------------------------
# Invariant tests
# ---------------------------------------------------------------------------

INSTANCES = {
    "toy_a": _toy_a,
    "toy_b": _toy_b,
    "toy_c": _toy_c,
    "toy_d": _toy_d,
    "random_24h": _random_24h_instance,
}


def _assert_invariants(plan, args: dict) -> None:
    H = len(args["pv"])
    battery: BatterySpec = args["battery"]
    diesel: DieselSpec = args["diesel"]
    dg_on_prev = args.get("dg_on_prev", False)

    assert plan.solve_status == "Optimal"
    assert plan.solve_ms > 0

    soc_min_kwh = battery.soc_min_pct / 100.0 * battery.capacity_kwh
    soc_max_kwh = battery.soc_max_pct / 100.0 * battery.capacity_kwh

    for t in range(H):
        lhs = plan.pv_use_kw[t] + plan.wind_use_kw[t] + plan.dg_kw[t] + plan.p_discharge_kw[t]
        rhs = (
            (args["critical"][t] - plan.unserved_critical_kwh[t])
            + (args["essential"][t] - plan.unserved_essential_kwh[t])
            + (args["deferrable"][t] - plan.unserved_deferrable_kwh[t])
            + plan.p_charge_kw[t]
        )
        assert lhs == pytest.approx(rhs, abs=1e-6), f"C1 balance violated at t={t}"

        assert soc_min_kwh - 1e-6 <= plan.soc_kwh[t] <= soc_max_kwh + 1e-6

        if plan.dg_on[t] == 0:
            assert plan.dg_kw[t] == pytest.approx(0.0, abs=1e-6)
        else:
            lo = diesel.min_load_frac * diesel.rated_kw
            hi = diesel.rated_kw
            assert lo - 1e-6 <= plan.dg_kw[t] <= hi + 1e-6

        assert plan.pv_use_kw[t] <= args["pv"][t] + 1e-6
        assert plan.wind_use_kw[t] <= args["wind"][t] + 1e-6

    prev = 1 if dg_on_prev else 0
    expected_starts = 0
    for t in range(H):
        if plan.dg_on[t] == 1 and prev == 0:
            expected_starts += 1
        prev = plan.dg_on[t]
    assert sum(plan.dg_start) == expected_starts


@pytest.mark.parametrize("name", list(INSTANCES.keys()))
def test_invariants(name):
    args = INSTANCES[name]()
    plan = _solve(args)
    _assert_invariants(plan, args)


# ---------------------------------------------------------------------------
# Behavioural / sensitivity tests
# ---------------------------------------------------------------------------


def test_toy_b_diesel_price_sensitivity():
    args = _toy_b()
    plan1 = _solve(args)

    args2 = dict(args)
    args2["economics"] = _economics(diesel_price_inr_per_l=args["economics"].diesel_price_inr_per_l * 2)
    plan2 = _solve(args2)

    assert plan1.dg_on == plan2.dg_on
    assert plan1.dg_kw == pytest.approx(plan2.dg_kw, rel=1e-4)

    fuel_cost1 = args["economics"].diesel_price_inr_per_l * sum(plan1.fuel_l)
    fuel_cost2 = args2["economics"].diesel_price_inr_per_l * sum(plan2.fuel_l)
    assert fuel_cost2 == pytest.approx(2 * fuel_cost1, rel=1e-3)


def _reserve_test_instance(reserve_hours: int, k_uncertainty: float) -> dict:
    """24h instance with a genuine evening deficit: daytime PV surplus
    charges the battery, then an evening essential-load surge (17:00-23:59)
    with no PV forces the battery to discharge (free in the objective) down
    toward whatever floor is active, giving the reserve/k_uncertainty
    constraint (C9) real teeth to bind against."""
    H = 24
    hours = list(range(H))
    pv = [15.0 if 8 <= h <= 16 else 0.0 for h in hours]
    wind = [0.0] * H
    critical = [4.0] * H
    essential = [2.0 if h < 17 else 10.0 for h in hours]
    deferrable = [0.0] * H

    battery = _battery(
        capacity_kwh=60.0,
        soc_min_pct=10.0,
        soc_max_pct=95.0,
        p_charge_max_kw=20.0,
        p_discharge_max_kw=20.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_init_pct=60.0,
    )
    return dict(
        pv=pv,
        wind=wind,
        critical=critical,
        essential=essential,
        deferrable=deferrable,
        battery=battery,
        diesel=_diesel(rated_kw=8.0, min_load_frac=0.3),
        economics=_economics(),
        soc_init_kwh=36.0,
        dg_on_prev=False,
        reserve_hours=reserve_hours,
        k_uncertainty=k_uncertainty,
    )


def test_reserve_hours_raises_min_soc():
    plan_no_reserve = _solve(_reserve_test_instance(reserve_hours=0, k_uncertainty=1.0))
    plan_with_reserve = _solve(_reserve_test_instance(reserve_hours=3, k_uncertainty=1.0))

    assert plan_no_reserve.solve_status == "Optimal"
    assert plan_with_reserve.solve_status == "Optimal"
    assert min(plan_with_reserve.soc_kwh) >= min(plan_no_reserve.soc_kwh) - 1e-6


def test_k_uncertainty_raises_min_soc():
    plan_k1 = _solve(_reserve_test_instance(reserve_hours=3, k_uncertainty=1.0))
    plan_k2 = _solve(_reserve_test_instance(reserve_hours=3, k_uncertainty=2.0))

    assert plan_k1.solve_status == "Optimal"
    assert plan_k2.solve_status == "Optimal"
    assert min(plan_k2.soc_kwh) >= min(plan_k1.soc_kwh) - 1e-6
