"""
DIYA tests/test_simulator.py — Phase 3 invariant checks on simulate_hour
and simulate_hour_diesel_only across hand-picked hourly inputs: a surplus
case, deficit cases (including a full shedding-priority cascade), and an
exact-match case.
"""

from __future__ import annotations

import pytest

from core.simulator import simulate_hour, simulate_hour_diesel_only
from core.types import BatterySpec, DieselSpec


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


def _assert_balance(step, tol: float = 1e-6) -> None:
    lhs = step.pv_kw + step.wind_kw + step.dg_kw + step.batt_kw
    rhs = (
        (step.load_critical_kw - step.unserved_critical_kwh)
        + (step.load_essential_kw - step.unserved_essential_kwh)
        + (step.load_deferrable_kw - step.unserved_deferrable_kwh)
    )
    assert lhs == pytest.approx(rhs, abs=tol)


def _assert_soc_bounds(step, battery: BatterySpec, tol: float = 1e-6) -> None:
    soc_min_kwh = battery.soc_min_pct / 100.0 * battery.capacity_kwh
    soc_max_kwh = battery.soc_max_pct / 100.0 * battery.capacity_kwh
    assert soc_min_kwh - tol <= step.soc_kwh <= soc_max_kwh + tol


def test_surplus_case_charges_battery_no_curtailment():
    battery = _battery(capacity_kwh=50.0, soc_min_pct=10.0, soc_max_pct=95.0, p_charge_max_kw=50.0, eta_charge=1.0)
    diesel = _diesel()

    step = simulate_hour(
        t_label="t0",
        u_dg=False,
        pv_actual_kw=20.0,
        wind_actual_kw=0.0,
        load_critical_kw=3.0,
        load_essential_kw=2.0,
        load_deferrable_kw=0.0,
        soc_prev_kwh=25.0,
        battery=battery,
        diesel=diesel,
    )

    assert step.dg_on == 0
    assert step.dg_kw == 0.0
    assert step.fuel_l == 0.0
    assert step.curtailed_kwh == pytest.approx(0.0, abs=1e-9)
    assert step.batt_kw == pytest.approx(-15.0, rel=1e-6)  # charging at 15kW
    assert step.soc_kwh == pytest.approx(40.0, rel=1e-6)
    assert step.unserved_critical_kwh == 0.0
    assert step.unserved_essential_kwh == 0.0
    _assert_balance(step)
    _assert_soc_bounds(step, battery)


def test_exact_match_case_no_battery_action_no_diesel():
    battery = _battery(soc_init_pct=50.0)
    diesel = _diesel()

    step = simulate_hour(
        t_label="t0",
        u_dg=False,
        pv_actual_kw=5.0,
        wind_actual_kw=0.0,
        load_critical_kw=3.0,
        load_essential_kw=2.0,
        load_deferrable_kw=0.0,
        soc_prev_kwh=25.0,
        battery=battery,
        diesel=diesel,
    )

    assert step.dg_on == 0
    assert step.fuel_l == 0.0
    assert step.curtailed_kwh == pytest.approx(0.0, abs=1e-9)
    assert step.batt_kw == pytest.approx(0.0, abs=1e-9)
    assert step.soc_kwh == pytest.approx(25.0, rel=1e-6)  # unchanged
    assert step.unserved_critical_kwh == 0.0
    assert step.unserved_essential_kwh == 0.0
    _assert_balance(step)
    _assert_soc_bounds(step, battery)


def test_deficit_case_diesel_capped_partial_shed_protects_critical():
    """Deficit large enough that diesel (capped at rated) + a frozen
    battery can't cover it, but shedding deferrable first is enough to
    protect essential from full loss and critical entirely."""
    battery = _battery(capacity_kwh=50.0, soc_min_pct=10.0, soc_max_pct=95.0, p_discharge_max_kw=0.0)
    diesel = _diesel(rated_kw=10.0, min_load_frac=0.3)

    soc_min_kwh = battery.soc_min_pct / 100.0 * battery.capacity_kwh  # 5.0
    step = simulate_hour(
        t_label="t0",
        u_dg=True,
        pv_actual_kw=0.0,
        wind_actual_kw=0.0,
        load_critical_kw=5.0,
        load_essential_kw=10.0,
        load_deferrable_kw=8.0,
        soc_prev_kwh=soc_min_kwh,  # nothing available to discharge
        battery=battery,
        diesel=diesel,
    )

    # total_load=23, p_dg capped at rated=10, remaining_deficit=13
    assert step.dg_on == 1
    assert step.dg_kw == pytest.approx(10.0, rel=1e-6)
    assert step.unserved_deferrable_kwh == pytest.approx(8.0, rel=1e-6)  # shed first, fully
    assert step.unserved_essential_kwh == pytest.approx(5.0, rel=1e-6)  # partially shed
    assert step.unserved_critical_kwh == pytest.approx(0.0, abs=1e-9)  # protected
    _assert_balance(step)
    _assert_soc_bounds(step, battery)


def test_deficit_case_full_shedding_cascade_hits_critical_last():
    """No diesel, no battery, deficit exceeds even deferrable+essential:
    confirms critical is only shed after deferrable AND essential are both
    fully exhausted (the priority order, not just partial amounts)."""
    battery = _battery(capacity_kwh=50.0, soc_min_pct=10.0, soc_max_pct=95.0, p_discharge_max_kw=0.0)
    diesel = _diesel(rated_kw=10.0, min_load_frac=0.3)
    soc_min_kwh = battery.soc_min_pct / 100.0 * battery.capacity_kwh

    step = simulate_hour(
        t_label="t0",
        u_dg=False,
        pv_actual_kw=0.0,
        wind_actual_kw=0.0,
        load_critical_kw=5.0,
        load_essential_kw=10.0,
        load_deferrable_kw=8.0,
        soc_prev_kwh=soc_min_kwh,
        battery=battery,
        diesel=diesel,
    )

    assert step.dg_on == 0
    assert step.unserved_deferrable_kwh == pytest.approx(8.0, rel=1e-6)
    assert step.unserved_essential_kwh == pytest.approx(10.0, rel=1e-6)
    assert step.unserved_critical_kwh == pytest.approx(5.0, rel=1e-6)
    _assert_balance(step)
    _assert_soc_bounds(step, battery)


def test_battery_discharge_covers_deficit_after_diesel_min_load():
    battery = _battery(capacity_kwh=50.0, soc_min_pct=10.0, soc_max_pct=95.0, p_discharge_max_kw=50.0, eta_discharge=1.0)
    diesel = _diesel(rated_kw=10.0, min_load_frac=0.3)

    step = simulate_hour(
        t_label="t0",
        u_dg=True,
        pv_actual_kw=0.0,
        wind_actual_kw=0.0,
        load_critical_kw=5.0,
        load_essential_kw=10.0,
        load_deferrable_kw=0.0,
        soc_prev_kwh=40.0,
        battery=battery,
        diesel=diesel,
    )

    # total_load=15, p_dg=10 (rated), remaining_load_after_dg=5, battery covers it
    assert step.dg_kw == pytest.approx(10.0, rel=1e-6)
    assert step.batt_kw == pytest.approx(5.0, rel=1e-6)
    assert step.unserved_critical_kwh == 0.0
    assert step.unserved_essential_kwh == 0.0
    assert step.soc_kwh == pytest.approx(35.0, rel=1e-6)
    _assert_balance(step)
    _assert_soc_bounds(step, battery)


def test_fuel_formula_matches():
    battery = _battery(p_discharge_max_kw=50.0)
    diesel = _diesel(rated_kw=10.0, min_load_frac=0.3, fuel_a_l_per_kw_h=0.08, fuel_b_l_per_kwh=0.25)

    step = simulate_hour(
        t_label="t0",
        u_dg=True,
        pv_actual_kw=0.0,
        wind_actual_kw=0.0,
        load_critical_kw=5.0,
        load_essential_kw=0.0,
        load_deferrable_kw=0.0,
        soc_prev_kwh=25.0,
        battery=battery,
        diesel=diesel,
    )
    expected_fuel = diesel.fuel_a_l_per_kw_h * diesel.rated_kw * 1 + diesel.fuel_b_l_per_kwh * step.dg_kw
    assert step.fuel_l == pytest.approx(expected_fuel, rel=1e-9)


# ---------------------------------------------------------------------------
# simulate_hour_diesel_only
# ---------------------------------------------------------------------------


def test_diesel_only_no_renewables_no_battery():
    battery = _battery(capacity_kwh=50.0, soc_init_pct=20.0)
    diesel = _diesel(rated_kw=8.0, min_load_frac=0.3, fuel_a_l_per_kw_h=0.08, fuel_b_l_per_kwh=0.25)
    soc_prev_kwh = 10.0

    step = simulate_hour_diesel_only(
        t_label="t0",
        load_critical_kw=5.0,
        load_essential_kw=3.0,
        load_deferrable_kw=2.0,
        soc_prev_kwh=soc_prev_kwh,
        battery=battery,
        diesel=diesel,
    )

    assert step.pv_kw == 0.0
    assert step.wind_kw == 0.0
    assert step.batt_kw == 0.0
    assert step.curtailed_kwh == 0.0
    assert step.dg_on == 1
    assert step.soc_kwh == pytest.approx(soc_prev_kwh)  # frozen/unused

    # total_load=10, diesel rated=8 -> capped, remaining_deficit=2, shed from deferrable first
    assert step.dg_kw == pytest.approx(8.0, rel=1e-6)
    assert step.unserved_deferrable_kwh == pytest.approx(2.0, rel=1e-6)
    assert step.unserved_essential_kwh == pytest.approx(0.0, abs=1e-9)
    assert step.unserved_critical_kwh == pytest.approx(0.0, abs=1e-9)

    expected_fuel = diesel.fuel_a_l_per_kw_h * diesel.rated_kw + diesel.fuel_b_l_per_kwh * step.dg_kw
    assert step.fuel_l == pytest.approx(expected_fuel, rel=1e-9)
    _assert_balance(step)


def test_diesel_only_serves_load_within_capacity_exactly():
    battery = _battery()
    diesel = _diesel(rated_kw=10.0, min_load_frac=0.3)

    step = simulate_hour_diesel_only(
        t_label="t0",
        load_critical_kw=4.0,
        load_essential_kw=0.0,
        load_deferrable_kw=0.0,
        soc_prev_kwh=25.0,
        battery=battery,
        diesel=diesel,
    )

    assert step.dg_kw == pytest.approx(4.0, rel=1e-6)  # within [3,10], meets load exactly
    assert step.unserved_critical_kwh == 0.0
    _assert_balance(step)
