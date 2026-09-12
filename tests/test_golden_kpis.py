"""
DIYA tests/test_golden_kpis.py — regression protection: S1 (full 168h) KPIs
for all four policies must match a golden snapshot within 1% relative
tolerance on every numeric field. The first run creates the golden file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core.runner import run_scenario_policy
from core.types import load_site_config

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
SCENARIOS_YAML_PATH = REPO_ROOT / "config" / "scenarios.yaml"
GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "kpi_s1.json"

POLICIES = ["diesel_only", "rule_based", "mpc", "perfect_foresight"]
MPC_TEST_TIME_LIMIT_S = 2.0

# Wall-clock solve timing, not a deterministic model output -- these vary
# run-to-run with system load regardless of whether the code regressed, so
# they're excluded from the golden comparison (every other KPI field is
# deterministic given the same code/config/scenario data and is checked).
_TIMING_FIELDS = {"solve_ms_mean", "solve_ms_p95"}

# batt_equivalent_full_cycles is provably solver-tie-breaking-sensitive, not
# a cost/reliability metric: the objective (deliberately, see core/milp.py's
# curtailment-epsilon comment from Phase 2) prices fuel/CO2/starts/VOLL but
# never prices battery throughput itself, so whenever multiple equally
# cost-optimal dispatch trajectories exist, different solvers -- or the same
# solver with a different presolve/branching path -- can push different
# amounts of energy through the battery to reach the identical objective
# value. Confirmed empirically during Phase B's CBC->HiGHS solver-backend
# swap: switching perfect_foresight's solver (168h horizons route to HiGHS,
# see HIGHS_MIN_HORIZON_HOURS) moved S1's battery cycles from 5.85 to 6.04
# (+3.2%) while cost_total_inr moved by only 0.05% and every other KPI field
# stayed within ~0.2% -- i.e. an equally-cheap alternate optimum, not a
# regression. Excluded from the strict 1% check for the same reason
# solve_ms is: it's not a claim this test is positioned to verify.
_SOLVER_TIEBREAK_FIELDS = {"batt_equivalent_full_cycles"}


def _scenario_cfg(scenario_id: str) -> dict:
    raw = yaml.safe_load(SCENARIOS_YAML_PATH.read_text(encoding="utf-8"))["scenarios"]
    return next(s for s in raw if s["id"] == scenario_id)


def test_golden_kpis_s1_full_168h():
    site = load_site_config(SITE_CONFIG_PATH)
    scenario_cfg = _scenario_cfg("S1")

    current = {}
    for policy in POLICIES:
        run = run_scenario_policy("S1", policy, site, scenario_cfg, mpc_time_limit_s=MPC_TEST_TIME_LIMIT_S)
        current[policy] = run.kpi.model_dump()

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not GOLDEN_PATH.exists():
        GOLDEN_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
        print("\nGolden file created — rerun to verify regression protection.")
        return

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    for policy in POLICIES:
        for field, golden_val in golden[policy].items():
            if field in _TIMING_FIELDS or field in _SOLVER_TIEBREAK_FIELDS:
                continue
            current_val = current[policy][field]
            if isinstance(golden_val, (int, float)):
                assert current_val == pytest.approx(golden_val, rel=0.01, abs=1e-6), (
                    f"{policy}.{field}: {current_val} != golden {golden_val} (>1% relative diff)"
                )
            else:
                assert current_val == golden_val
