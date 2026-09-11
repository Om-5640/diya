"""
DIYA scripts/precompute_runs.py — Phase 3 precompute.

Runs all 3 scenarios x 4 policies through the real dispatch pipeline (no
more Phase 0 mocks) and writes RunResult JSON to data/processed/runs/ and
web/public/runs/, overwriting Phase 0's mock files in the latter.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from core.runner import run_scenario_policy  # noqa: E402
from core.types import load_site_config  # noqa: E402

SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
SCENARIOS_YAML_PATH = REPO_ROOT / "config" / "scenarios.yaml"
OUTPUT_DIRS = [REPO_ROOT / "data" / "processed" / "runs", REPO_ROOT / "web" / "public" / "runs"]

POLICIES = ["diesel_only", "rule_based", "mpc", "perfect_foresight"]


def main() -> None:
    t_start = time.perf_counter()

    site = load_site_config(SITE_CONFIG_PATH)
    scenarios_raw = yaml.safe_load(SCENARIOS_YAML_PATH.read_text(encoding="utf-8"))["scenarios"]
    scenario_cfgs = {s["id"]: s for s in scenarios_raw}

    for out_dir in OUTPUT_DIRS:
        out_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    rows = []

    for scenario_id, scenario_cfg in scenario_cfgs.items():
        for policy in POLICIES:
            t0 = time.perf_counter()
            run = run_scenario_policy(scenario_id, policy, site, scenario_cfg)
            elapsed = time.perf_counter() - t0
            results[(scenario_id, policy)] = run

            for out_dir in OUTPUT_DIRS:
                path = out_dir / f"{scenario_id}_{policy}.json"
                path.write_text(run.model_dump_json(indent=2), encoding="utf-8")

            kpi = run.kpi
            rows.append((scenario_id, policy, kpi, elapsed))
            print(f"  {scenario_id}/{policy} done in {elapsed:.1f}s")

    print("\n=== Summary ===")
    header = (
        f"{'scenario':8} {'policy':18} {'diesel_l':>10} {'cost_total_inr':>15} "
        f"{'co2_kg':>9} {'ren_frac':>9} {'outage_h':>9} {'dg_starts':>9} {'solve_ms_mean':>14}"
    )
    print(header)
    for scenario_id, policy, kpi, _elapsed in rows:
        print(
            f"{scenario_id:8} {policy:18} {kpi.diesel_l:10.2f} {kpi.cost_total_inr:15.2f} "
            f"{kpi.co2_kg:9.2f} {kpi.renewable_frac:9.3f} {kpi.critical_outage_hours:9.1f} "
            f"{kpi.dg_starts:9d} {kpi.solve_ms_mean:14.2f}"
        )

    print("\n=== Benefit capture (mpc's share of the gap between rule_based and perfect_foresight) ===")
    for scenario_id in scenario_cfgs:
        kpi_rb = results[(scenario_id, "rule_based")].kpi
        kpi_mpc = results[(scenario_id, "mpc")].kpi
        kpi_pf = results[(scenario_id, "perfect_foresight")].kpi
        denom = max(1e-6, kpi_rb.cost_total_inr - kpi_pf.cost_total_inr)
        benefit_capture = (kpi_rb.cost_total_inr - kpi_mpc.cost_total_inr) / denom
        print(f"  {scenario_id}: benefit_capture = {benefit_capture:.3f}")
        if not (0.0 <= benefit_capture <= 1.0):
            print(
                f"  WARNING: {scenario_id} benefit_capture={benefit_capture:.3f} is outside [0,1] "
                "-- mpc beating its own theoretical upper bound is a modeling bug to investigate, "
                "not a result to report."
            )

    total_elapsed = time.perf_counter() - t_start
    print(f"\nTotal wall-clock time: {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
