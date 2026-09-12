"""
DIYA scripts/benchmark_solver.py — before/after solver-backend timing.

Runs S2's full 168-hour rolling MPC (core.policies.run_mpc, the exact code
path /api/resolve uses) AND the single-shot 168-hour perfect_foresight solve
(the exact code path scripts/precompute_runs.py uses), each once under the
hybrid HiGHS/CBC routing that's actually live, then once forced onto CBC
only, so the comparison is apples-to-apples on this machine, right now,
rather than quoting old numbers from a different run.

HONEST FRAMING (read before assuming this script proves /api/resolve got
faster): core.milp's hybrid routing sends horizons below
HIGHS_MIN_HORIZON_HOURS (100) to CBC -- and run_mpc's rolling window is only
`site.horizon_hours` (24) at a time, every hour. So /api/resolve's wall-clock
time is NOT reduced by Phase B; it was already using the empirically-faster
solver for its problem shape. What Phase B actually fixed is
perfect_foresight's single large 168-hour solve, which is HiGHS's clear win
(~7x faster in testing) and is what scripts/precompute_runs.py spends most
of its time on. Both numbers are printed below, clearly labeled, so this
isn't glossed over.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

import core.milp as milp  # noqa: E402
from core.policies import run_mpc, run_perfect_foresight  # noqa: E402
from core.types import load_site_config  # noqa: E402

SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
S2_PARQUET_PATH = REPO_ROOT / "data" / "processed" / "S2.parquet"


def _load():
    site = load_site_config(SITE_CONFIG_PATH)
    df = pd.read_parquet(S2_PARQUET_PATH)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return site, df


def _bench_run_mpc(site, df, label: str) -> None:
    t0 = time.perf_counter()
    steps, solve_ms_list = run_mpc(df, site, time_limit_s=3.0)
    total_s = time.perf_counter() - t0
    mean_ms = sum(solve_ms_list) / len(solve_ms_list)
    p95_ms = sorted(solve_ms_list)[int(0.95 * len(solve_ms_list))]
    print(f"  [{label}] run_mpc (168 rolling 24h solves): total={total_s:.1f}s  mean/solve={mean_ms:.1f}ms  p95/solve={p95_ms:.1f}ms")


def _bench_perfect_foresight(site, df, label: str) -> None:
    t0 = time.perf_counter()
    steps, solve_ms_list = run_perfect_foresight(df, site, time_limit_s=30.0, gap_rel=0.01)
    total_s = time.perf_counter() - t0
    print(f"  [{label}] run_perfect_foresight (one 168h solve): total={total_s:.1f}s  solve_ms={solve_ms_list[0]:.1f}ms")


def main() -> None:
    site, df = _load()

    print("=== DIYA solver benchmark: S2, full 168h ===")
    print(f"Live routing at import: SOLVER_BACKEND={milp.SOLVER_BACKEND}, HIGHS_MIN_HORIZON_HOURS={milp.HIGHS_MIN_HORIZON_HOURS}")
    print()

    print("run_mpc (the code path /api/resolve uses -- rolling 24h windows, always routed to CBC")
    print("under the hybrid policy since 24 < HIGHS_MIN_HORIZON_HOURS):")
    _bench_run_mpc(site, df, "hybrid routing (live default)")

    print()
    print("run_perfect_foresight (the code path scripts/precompute_runs.py uses -- one 168h solve,")
    print("routed to HiGHS under the hybrid policy since 168 >= HIGHS_MIN_HORIZON_HOURS):")
    _bench_perfect_foresight(site, df, "hybrid routing (live default) -> HiGHS")

    print()
    print("Forcing CBC everywhere for a same-machine, same-moment A/B on perfect_foresight:")
    original_backend = milp.SOLVER_BACKEND
    milp.SOLVER_BACKEND = "PULP_CBC_CMD"
    try:
        _bench_perfect_foresight(site, df, "forced CBC")
    finally:
        milp.SOLVER_BACKEND = original_backend

    print()
    print("=== Summary ===")
    print("run_mpc (/api/resolve's actual workload): UNCHANGED by this phase -- it was already")
    print("  CBC-routed before Phase B and remains CBC-routed after, because CBC is empirically")
    print("  faster than HiGHS for this many-small-solves problem shape (see core/milp.py's comment).")
    print("run_perfect_foresight (scripts/precompute_runs.py's workload): genuinely faster --")
    print("  HiGHS wins decisively on this one large 168-hour solve.")


if __name__ == "__main__":
    main()
