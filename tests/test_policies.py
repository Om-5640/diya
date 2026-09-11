"""
DIYA tests/test_policies.py — Phase 3 policy tests: energy balance and SOC
bounds invariants on all four policies (S1, max_hours=48 for speed), reason
code set checks, and THE GATE TEST on S2's full 168 hours (the single most
important check in this phase: mpc must beat rule_based, and must never
beat perfect_foresight).

mpc uses time_limit_s=2.0 for its rolling per-hour solves throughout this
file to keep runtime down; precompute_runs.py uses 3.0 for the real run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.runner import run_scenario_policy
from core.types import RunResult, SiteConfig, load_site_config

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
SCENARIOS_YAML_PATH = REPO_ROOT / "config" / "scenarios.yaml"

MPC_TEST_TIME_LIMIT_S = 2.0

POLICIES = ["diesel_only", "rule_based", "mpc", "perfect_foresight"]

RULE_BASED_ALLOWED_CODES = {
    "R8_CRITICAL_DEFICIT",
    "R2_SOLAR_SURPLUS",
    "R5_MINLOAD",
    "R7_SHED_DEFERRABLE",
    "R6_AVOID_START",
    "R3_CHEAPER_DIESEL",
    "R0_NOMINAL",
}


def _site() -> SiteConfig:
    return load_site_config(SITE_CONFIG_PATH)


def _scenario_cfg(scenario_id: str) -> dict:
    raw = yaml.safe_load(SCENARIOS_YAML_PATH.read_text(encoding="utf-8"))["scenarios"]
    return next(s for s in raw if s["id"] == scenario_id)


def _run(scenario_id: str, policy: str, max_hours: int | None = None) -> RunResult:
    return run_scenario_policy(
        scenario_id,
        policy,
        _site(),
        _scenario_cfg(scenario_id),
        max_hours=max_hours,
        mpc_time_limit_s=MPC_TEST_TIME_LIMIT_S,
    )


# diesel_only/rule_based are exact closed-form arithmetic (tight tolerance
# is meaningful); mpc/perfect_foresight go through CBC's LP solve, which
# carries small floating-point residuals at the ~1e-6 scale that are not
# formulation bugs -- 1e-4 is still far tighter than any real bug would be.
_BALANCE_TOL = {
    "diesel_only": 1e-6,
    "rule_based": 1e-6,
    "mpc": 1e-4,
    "perfect_foresight": 1e-4,
}


def _assert_balance(step, policy: str) -> None:
    lhs = step.pv_kw + step.wind_kw + step.dg_kw + step.batt_kw
    rhs = (
        (step.load_critical_kw - step.unserved_critical_kwh)
        + (step.load_essential_kw - step.unserved_essential_kwh)
        + (step.load_deferrable_kw - step.unserved_deferrable_kwh)
    )
    tol = _BALANCE_TOL[policy]
    if policy == "diesel_only":
        # simulate_hour_diesel_only's min-load floor can force dg_kw above
        # what's needed on low-load hours (no battery/curtailment to absorb
        # it, curtailed_kwh is always 0 for this baseline by design) -- the
        # naive baseline genuinely wastes fuel here, which is itself a real
        # point about why this baseline is the worst KPI performer. Assert
        # dg_kw can only be >= what's needed, never short of it.
        assert lhs >= rhs - tol, f"balance shortfall at {step.t}: lhs={lhs} rhs={rhs}"
    else:
        assert lhs == pytest.approx(rhs, abs=tol), f"balance violated at {step.t}: lhs={lhs} rhs={rhs}"


@pytest.fixture(scope="module")
def s1_runs() -> dict[str, RunResult]:
    return {policy: _run("S1", policy, max_hours=48) for policy in POLICIES}


@pytest.mark.parametrize("policy", POLICIES)
def test_energy_balance_holds_every_step(s1_runs, policy):
    run = s1_runs[policy]
    for step in run.steps:
        _assert_balance(step, policy)


@pytest.mark.parametrize("policy", POLICIES)
def test_soc_within_bounds_every_step(s1_runs, policy):
    site = _site()
    soc_min = site.battery.soc_min_pct
    soc_max = site.battery.soc_max_pct
    run = s1_runs[policy]
    for step in run.steps:
        assert soc_min - 1e-6 <= step.soc_pct <= soc_max + 1e-6, f"soc_pct {step.soc_pct} out of bounds at {step.t}"


def test_diesel_only_never_touches_renewables_or_battery(s1_runs):
    run = s1_runs["diesel_only"]
    for step in run.steps:
        assert step.pv_kw == 0.0
        assert step.wind_kw == 0.0
        assert step.batt_kw == 0.0


def test_rule_based_reason_codes_are_forecast_free_subset(s1_runs):
    run = s1_runs["rule_based"]
    codes = {step.reason_code for step in run.steps}
    assert codes <= RULE_BASED_ALLOWED_CODES, f"rule_based emitted forecast-aware codes: {codes - RULE_BASED_ALLOWED_CODES}"
    assert "R1_PREPOSITION" not in codes
    assert "R4_RESERVE_HOLD" not in codes


def test_mpc_can_emit_forecast_aware_codes():
    """mpc CAN emit R1_PREPOSITION or R4_RESERVE_HOLD -- schedule-dependent,
    so this tries S2 (the scenario designed to stress the reserve/
    prepositioning logic) and warns rather than hard-failing if neither
    ever fires."""
    run = _run("S2", "mpc", max_hours=48)
    codes = {step.reason_code for step in run.steps}
    forecast_aware = {"R1_PREPOSITION", "R4_RESERVE_HOLD"} & codes
    if not forecast_aware:
        print("\nWARNING: mpc never emitted R1_PREPOSITION or R4_RESERVE_HOLD on S2[:48h] -- schedule-dependent, not a hard failure.")
    assert forecast_aware, "mpc never emitted a forecast-aware reason code on S2[:48h]"


# ---------------------------------------------------------------------------
# THE GATE TEST
# ---------------------------------------------------------------------------


def _print_kpi_table(runs: dict[str, RunResult]) -> None:
    print("\n=== S2 (full 168h) gate-test KPI table ===")
    fields = [
        "diesel_l",
        "cost_fuel_inr",
        "cost_total_inr",
        "co2_kg",
        "renewable_frac",
        "unserved_critical_kwh",
        "unserved_total_kwh",
        "critical_outage_hours",
        "dg_starts",
        "dg_run_hours",
        "batt_equivalent_full_cycles",
        "cost_per_delivered_kwh_inr",
        "served_kwh",
        "solve_ms_mean",
        "solve_ms_p95",
    ]
    header = f"{'field':30}" + "".join(f"{p:>20}" for p in POLICIES)
    print(header)
    for field in fields:
        row = f"{field:30}"
        for policy in POLICIES:
            val = getattr(runs[policy].kpi, field)
            row += f"{val:20.4f}" if isinstance(val, float) else f"{val:20d}"
        print(row)


def test_gate_s2_full_168h_mpc_beats_rule_based_and_never_beats_perfect_foresight():
    runs = {policy: _run("S2", policy) for policy in POLICIES}
    _print_kpi_table(runs)

    kpi_rb = runs["rule_based"].kpi
    kpi_mpc = runs["mpc"].kpi
    kpi_pf = runs["perfect_foresight"].kpi

    assert kpi_mpc.cost_total_inr < kpi_rb.cost_total_inr, (
        f"mpc cost {kpi_mpc.cost_total_inr:.2f} did not beat rule_based cost {kpi_rb.cost_total_inr:.2f}"
    )
    assert kpi_mpc.critical_outage_hours <= kpi_rb.critical_outage_hours, (
        f"mpc outage hours {kpi_mpc.critical_outage_hours} exceeded rule_based {kpi_rb.critical_outage_hours}"
    )
    assert kpi_pf.cost_total_inr <= kpi_mpc.cost_total_inr + 1e-3, (
        f"perfect_foresight cost {kpi_pf.cost_total_inr:.2f} exceeded mpc cost {kpi_mpc.cost_total_inr:.2f} -- bug"
    )
