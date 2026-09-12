"""
DIYA scripts/build_scenarios.py — Khavda's data layer orchestration (Phase 1A;
thin wrapper since DIYA v2 Phase E).

Fetches/caches Khavda's fixed 2025-01-01..2025-12-31 weather year, computes
actual PV and wind power, diagnoses every week, auto-selects the S1/S2/S3
scenario windows, patches those dates into config/scenarios.yaml (preserving
its hand-written names/descriptions -- only start_date/end_date change), and
writes data/processed/{scenario_id}.parquet for each scenario.

The actual computation lives in core/site_pipeline.py::build_site_scenarios,
shared with every other site's pipeline (DIYA v2 Phase E). This script calls
it with khavda's exact historical window (weather_year_end=2025-12-31, the
default 365-day window) and generate_scenarios_yaml=False, so it reproduces
today's exact output -- unchanged -- rather than overwriting
config/scenarios.yaml's hand-written wording with generic text.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from core.forecast import forecast_error_metrics  # noqa: E402
from core.site_pipeline import build_site_scenarios  # noqa: E402
from core.types import load_site_config  # noqa: E402

SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
SCENARIOS_YAML_PATH = REPO_ROOT / "config" / "scenarios.yaml"
OUTPUT_DIR = REPO_ROOT / "data" / "processed"

# Khavda's fixed, already-committed weather year -- an explicit override so
# this script's output never drifts with "today", regardless of when it's
# actually re-run. See core/site_pipeline.py::build_site_scenarios's
# weather_year_end docstring.
KHAVDA_WEATHER_YEAR_END = date(2025, 12, 31)


def _update_scenarios_yaml(text: str, scenario_id: str, start_date: str, end_date: str) -> str:
    pattern = re.compile(
        rf'(- id: {re.escape(scenario_id)}\b.*?)'
        rf'start_date:\s*"[^"]*"(?:\s*#[^\n]*)?'
        rf'(\s*\n\s*)'
        rf'end_date:\s*"[^"]*"(?:\s*#[^\n]*)?',
        re.DOTALL,
    )

    def _sub(m: re.Match) -> str:
        return f'{m.group(1)}start_date: "{start_date}"{m.group(2)}end_date: "{end_date}"'

    new_text, count = pattern.subn(_sub, text, count=1)
    if count != 1:
        raise ValueError(f"could not locate start_date/end_date block for scenario {scenario_id}")
    return new_text


def _print_scenario_summary(scenario_id: str, df: pd.DataFrame) -> None:
    total_load_kwh = (df["load_critical_kw"] + df["load_essential_kw"] + df["load_deferrable_kw"]).sum()
    total_pv_kwh = df["pv_actual_kw"].sum()
    total_wind_kwh = df["wind_actual_kw"].sum()
    renewable_ratio = (total_pv_kwh + total_wind_kwh) / total_load_kwh if total_load_kwh > 0 else float("nan")
    peak_load_kw = (df["load_critical_kw"] + df["load_essential_kw"] + df["load_deferrable_kw"]).max()

    pv_actual = pd.Series(df["pv_actual_kw"].to_numpy(), name="pv_kw")
    pv_fc = pd.Series(df["pv_fc_kw"].to_numpy(), name="pv_kw_fc")
    wind_actual = pd.Series(df["wind_actual_kw"].to_numpy(), name="wind_kw")
    wind_fc = pd.Series(df["wind_fc_kw"].to_numpy(), name="wind_kw_fc")
    pv_metrics = forecast_error_metrics(pv_fc, pv_actual)["overall"]
    wind_metrics = forecast_error_metrics(wind_fc, wind_actual)["overall"]

    print(f"\n--- {scenario_id} summary ---")
    print(f"  total load:        {total_load_kwh:9.1f} kWh")
    print(f"  total PV:          {total_pv_kwh:9.1f} kWh")
    print(f"  total wind:        {total_wind_kwh:9.1f} kWh")
    print(f"  renewable/load:    {renewable_ratio:9.3f}")
    print(f"  peak load:         {peak_load_kw:9.2f} kW")
    print(
        f"  PV forecast error:   MAE={pv_metrics['mae']:.3f} kW  RMSE={pv_metrics['rmse']:.3f} kW  "
        f"bias={pv_metrics['bias']:+.3f} kW  nRMSE={pv_metrics['nrmse']:.3f}"
    )
    print(
        f"  wind forecast error: MAE={wind_metrics['mae']:.3f} kW  RMSE={wind_metrics['rmse']:.3f} kW  "
        f"bias={wind_metrics['bias']:+.3f} kW  nRMSE={wind_metrics['nrmse']:.3f}"
    )


def main() -> None:
    config = load_site_config(SITE_CONFIG_PATH)

    def progress_cb(stage: str, message: str) -> None:
        print(message)

    result = build_site_scenarios(
        "khavda",
        config,
        OUTPUT_DIR,
        weather_year_end=KHAVDA_WEATHER_YEAR_END,
        generate_scenarios_yaml=False,
        progress_cb=progress_cb,
    )

    print("\n=== Weekly diagnostics (2025) ===")
    print(
        result.weekly_diagnostics.to_string(
            index=False,
            columns=["week", "start_date", "end_date", "mean_daily_pv_kwh", "mean_daily_wind_kwh", "min_2day_pv_kwh"],
            float_format=lambda v: f"{v:.2f}",
        )
    )

    windows = {sid: (meta.start_date, meta.end_date) for sid, meta in result.scenario_windows.items()}
    print("\n=== Selected scenario windows ===")
    for scenario_id, (start, end) in windows.items():
        print(f"  {scenario_id}: {start} .. {end}")

    yaml_text = SCENARIOS_YAML_PATH.read_text(encoding="utf-8")
    for scenario_id, (start, end) in windows.items():
        yaml_text = _update_scenarios_yaml(yaml_text, scenario_id, start, end)
    SCENARIOS_YAML_PATH.write_text(yaml_text, encoding="utf-8")
    print(f"\nWrote selected dates to {SCENARIOS_YAML_PATH.relative_to(REPO_ROOT)}")

    for scenario_id, df in result.scenario_dataframes.items():
        _print_scenario_summary(scenario_id, df)


if __name__ == "__main__":
    main()
