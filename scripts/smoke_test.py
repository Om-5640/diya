"""
DIYA scripts/smoke_test.py — fast end-to-end sanity check ("did everything
survive a fresh clone"). Standalone script, not pytest; target <20s.

Checks: the 3 scenario parquets exist and load, all 12 precomputed run
JSONs exist in both data/processed/runs/ and web/public/runs/ with a
168-step "steps" array and a "kpi" key, and the API (in-process via
TestClient, no subprocess) answers 200 on /api/health, /api/runs, and
/api/runs/S2/mpc.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

SCENARIOS = ["S1", "S2", "S3"]
POLICIES = ["diesel_only", "rule_based", "mpc", "perfect_foresight"]
EXPECTED_STEPS = 168

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    line = f"  [{status}] {name}"
    if detail:
        line += f" -- {detail}"
    print(line)
    results.append((name, ok))


def check_scenario_parquets() -> None:
    for scenario_id in SCENARIOS:
        path = REPO_ROOT / "data" / "processed" / f"{scenario_id}.parquet"
        if not path.exists():
            check(f"{scenario_id}.parquet exists", False, f"missing: {path}")
            continue
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            check(f"{scenario_id}.parquet loads", False, str(e))
            continue
        check(f"{scenario_id}.parquet loads", len(df) == EXPECTED_STEPS, f"{len(df)} rows")


def check_run_json(base_dir: Path, label: str) -> None:
    for scenario_id in SCENARIOS:
        for policy in POLICIES:
            path = base_dir / f"{scenario_id}_{policy}.json"
            name = f"{label}/{scenario_id}_{policy}.json"
            if not path.exists():
                check(name, False, "missing")
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                check(name, False, f"invalid JSON: {e}")
                continue
            n_steps = len(data.get("steps", []))
            has_kpi = "kpi" in data
            ok = n_steps == EXPECTED_STEPS and has_kpi
            check(name, ok, f"steps={n_steps}, kpi={'present' if has_kpi else 'MISSING'}")


def check_api_in_process() -> None:
    try:
        from fastapi.testclient import TestClient

        import api.main as api_main
    except Exception as e:
        check("API app imports", False, str(e))
        return
    check("API app imports", True)

    client = TestClient(api_main.app)

    resp = client.get("/api/health")
    check("GET /api/health -> 200", resp.status_code == 200, f"got {resp.status_code}")

    resp = client.get("/api/runs")
    check("GET /api/runs -> 200", resp.status_code == 200, f"got {resp.status_code}")

    resp = client.get("/api/runs/S2/mpc")
    check("GET /api/runs/S2/mpc -> 200", resp.status_code == 200, f"got {resp.status_code}")


def main() -> None:
    print("=== DIYA smoke test ===")

    print("\nScenario parquets:")
    check_scenario_parquets()

    print("\nPrecomputed runs (data/processed/runs/):")
    check_run_json(REPO_ROOT / "data" / "processed" / "runs", "data/processed/runs")

    print("\nPrecomputed runs (web/public/runs/):")
    check_run_json(REPO_ROOT / "web" / "public" / "runs", "web/public/runs")

    print("\nAPI (in-process):")
    check_api_in_process()

    print()
    all_ok = all(ok for _, ok in results)
    if all_ok:
        print("SMOKE TEST: PASS")
        sys.exit(0)
    else:
        print("SMOKE TEST: FAIL — see above")
        sys.exit(1)


if __name__ == "__main__":
    main()
