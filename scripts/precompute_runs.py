"""
DIYA scripts/precompute_runs.py — Khavda's Phase 3 precompute (thin wrapper
since DIYA v2 Phase E).

Runs all 3 scenarios x 4 policies through the real dispatch pipeline and
writes RunResult JSON to data/processed/runs/ and web/public/runs/ (a
second copy for the frontend's static file serving -- the two-directory
mirroring is khavda/legacy-specific; core/site_pipeline.py's
precompute_site_runs only writes one runs_dir, matching every other site's
single-directory layout).

The actual computation lives in core/site_pipeline.py::precompute_site_runs,
shared with every other site's pipeline (DIYA v2 Phase E).
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.site_pipeline import precompute_site_runs  # noqa: E402
from core.types import load_site_config  # noqa: E402

SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
SCENARIOS_DIR = REPO_ROOT / "data" / "processed"
SCENARIOS_YAML_PATH = REPO_ROOT / "config" / "scenarios.yaml"
RUNS_DIR = REPO_ROOT / "data" / "processed" / "runs"
MIRROR_RUNS_DIR = REPO_ROOT / "web" / "public" / "runs"


def main() -> None:
    t_start = time.perf_counter()

    site = load_site_config(SITE_CONFIG_PATH)

    def progress_cb(stage: str, message: str) -> None:
        print(f"  {message}")

    result = precompute_site_runs(
        "khavda",
        site,
        SCENARIOS_DIR,
        RUNS_DIR,
        mpc_time_limit_s=3.0,
        scenarios_yaml_path=SCENARIOS_YAML_PATH,
        progress_cb=progress_cb,
    )

    MIRROR_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for (scenario_id, policy) in result.runs:
        filename = f"{scenario_id}_{policy}.json"
        shutil.copyfile(RUNS_DIR / filename, MIRROR_RUNS_DIR / filename)

    print("\n=== Summary ===")
    header = (
        f"{'scenario':8} {'policy':18} {'diesel_l':>10} {'cost_total_inr':>15} "
        f"{'co2_kg':>9} {'ren_frac':>9} {'outage_h':>9} {'dg_starts':>9} {'solve_ms_mean':>14}"
    )
    print(header)
    for (scenario_id, policy), run in result.runs.items():
        kpi = run.kpi
        print(
            f"{scenario_id:8} {policy:18} {kpi.diesel_l:10.2f} {kpi.cost_total_inr:15.2f} "
            f"{kpi.co2_kg:9.2f} {kpi.renewable_frac:9.3f} {kpi.critical_outage_hours:9.1f} "
            f"{kpi.dg_starts:9d} {kpi.solve_ms_mean:14.2f}"
        )

    scenario_ids = sorted({scenario_id for scenario_id, _policy in result.runs})
    print("\n=== Benefit capture (mpc's share of the gap between rule_based and perfect_foresight) ===")
    for scenario_id in scenario_ids:
        kpi_rb = result.runs[(scenario_id, "rule_based")].kpi
        kpi_mpc = result.runs[(scenario_id, "mpc")].kpi
        kpi_pf = result.runs[(scenario_id, "perfect_foresight")].kpi
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
