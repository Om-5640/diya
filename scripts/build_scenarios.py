"""
DIYA scripts/build_scenarios.py — data layer orchestration (Phase 1A).

Fetches/caches a full year of Khavda weather, computes actual PV and wind
power, diagnoses every week of the year, auto-selects the S1/S2/S3 scenario
windows, writes those dates back into config/scenarios.yaml, and writes
data/processed/{scenario_id}.parquet for each scenario. No optimization
logic — this is the data layer only.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from core.forecast import forecast_error_metrics, make_forecast  # noqa: E402
from core.load import generate_load  # noqa: E402
from core.pv import pv_power_kw  # noqa: E402
from core.types import load_site_config  # noqa: E402
from core.weather import fetch_weather  # noqa: E402
from core.wind import wind_power_kw  # noqa: E402

SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
SCENARIOS_YAML_PATH = REPO_ROOT / "config" / "scenarios.yaml"
CACHE_DIR = REPO_ROOT / "data" / "raw"
OUTPUT_DIR = REPO_ROOT / "data" / "processed"

YEAR_START = "2025-01-01"
YEAR_END = "2025-12-31"

SCENARIO_LOAD_MULTIPLIER = {"S1": 1.0, "S2": 1.0, "S3": 1.2}


def _seed_for(scenario_id: str, kind: str) -> int:
    digest = hashlib.sha256(f"{scenario_id}|{kind}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _weekly_diagnostics(pv_actual_kw: pd.Series, wind_actual_kw: pd.Series) -> pd.DataFrame:
    daily_pv = pv_actual_kw.resample("1D").sum()
    daily_wind = wind_actual_kw.resample("1D").sum()
    n_weeks = len(daily_pv) // 7

    rows = []
    for w in range(n_weeks):
        pv_week = daily_pv.iloc[w * 7 : (w + 1) * 7]
        wind_week = daily_wind.iloc[w * 7 : (w + 1) * 7]
        two_day_sums = [pv_week.iloc[i] + pv_week.iloc[i + 1] for i in range(len(pv_week) - 1)]
        rows.append(
            {
                "week": w,
                "start_date": pv_week.index[0].date().isoformat(),
                "end_date": pv_week.index[-1].date().isoformat(),
                "mean_daily_pv_kwh": pv_week.mean(),
                "mean_daily_wind_kwh": wind_week.mean(),
                "min_2day_pv_kwh": min(two_day_sums),
            }
        )
    return pd.DataFrame(rows)


def _select_scenario_weeks(diag: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    median_pv = diag["mean_daily_pv_kwh"].median()
    dist_to_median = (diag["mean_daily_pv_kwh"] - median_pv).abs()
    s1_row = diag.loc[dist_to_median.idxmin()]
    s2_row = diag.loc[diag["min_2day_pv_kwh"].idxmin()]
    return s1_row, s2_row


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


def _slice_week(df: pd.DataFrame | pd.Series, start_date: str, end_date: str):
    tz = df.index.tz
    start_ts = pd.Timestamp(start_date, tz=tz)
    end_ts = pd.Timestamp(end_date, tz=tz) + pd.Timedelta(hours=23)
    return df.loc[start_ts:end_ts]


def build_scenario_parquet(
    scenario_id: str,
    start_date: str,
    end_date: str,
    weather_df: pd.DataFrame,
    pv_actual_kw: pd.Series,
    wind_actual_kw: pd.Series,
) -> pd.DataFrame:
    weather_week = _slice_week(weather_df, start_date, end_date)
    pv_week = _slice_week(pv_actual_kw, start_date, end_date)
    wind_week = _slice_week(wind_actual_kw, start_date, end_date)

    load_multiplier = SCENARIO_LOAD_MULTIPLIER[scenario_id]
    load_df = generate_load(weather_week.index, seed=_seed_for(scenario_id, "load"), load_multiplier=load_multiplier)

    pv_fc = make_forecast(pv_week, seed=_seed_for(scenario_id, "pv_fc"), lead_hours=24)
    wind_fc = make_forecast(wind_week, seed=_seed_for(scenario_id, "wind_fc"), lead_hours=24)

    out = pd.DataFrame(
        {
            "timestamp": weather_week.index,
            "ghi": weather_week["ghi"].to_numpy(),
            "dni": weather_week["dni"].to_numpy(),
            "dhi": weather_week["dhi"].to_numpy(),
            "temp_c": weather_week["temp_c"].to_numpy(),
            "wind_ms": weather_week["wind_ms"].to_numpy(),
            "pv_actual_kw": pv_week.to_numpy(),
            "pv_fc_kw": pv_fc.to_numpy(),
            "wind_actual_kw": wind_week.to_numpy(),
            "wind_fc_kw": wind_fc.to_numpy(),
            "load_critical_kw": load_df["critical_kw"].to_numpy(),
            "load_essential_kw": load_df["essential_kw"].to_numpy(),
            "load_deferrable_kw": load_df["deferrable_kw"].to_numpy(),
        }
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{scenario_id}.parquet"
    out.to_parquet(out_path, engine="pyarrow", index=False)

    return out


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

    print(f"Fetching/loading cached weather for {config.name} ({YEAR_START} .. {YEAR_END})...")
    weather_df = fetch_weather(config.lat, config.lon, YEAR_START, YEAR_END, cache_dir=CACHE_DIR)
    print(f"  {len(weather_df)} hourly rows loaded.")

    pv_actual_kw = pv_power_kw(weather_df, config.pv, config.lat, config.lon, config.elevation_m, config.timezone)
    wind_actual_kw = wind_power_kw(weather_df, config.wind)

    diag = _weekly_diagnostics(pv_actual_kw, wind_actual_kw)
    print("\n=== Weekly diagnostics (2025) ===")
    print(
        diag.to_string(
            index=False,
            columns=["week", "start_date", "end_date", "mean_daily_pv_kwh", "mean_daily_wind_kwh", "min_2day_pv_kwh"],
            float_format=lambda v: f"{v:.2f}",
        )
    )

    s1_row, s2_row = _select_scenario_weeks(diag)
    windows = {
        "S1": (s1_row["start_date"], s1_row["end_date"]),
        "S2": (s2_row["start_date"], s2_row["end_date"]),
        "S3": (s1_row["start_date"], s1_row["end_date"]),
    }

    print("\n=== Selected scenario windows ===")
    for scenario_id, (start, end) in windows.items():
        print(f"  {scenario_id}: {start} .. {end}")

    yaml_text = SCENARIOS_YAML_PATH.read_text(encoding="utf-8")
    for scenario_id, (start, end) in windows.items():
        yaml_text = _update_scenarios_yaml(yaml_text, scenario_id, start, end)
    SCENARIOS_YAML_PATH.write_text(yaml_text, encoding="utf-8")
    print(f"\nWrote selected dates to {SCENARIOS_YAML_PATH.relative_to(REPO_ROOT)}")

    for scenario_id, (start, end) in windows.items():
        df = build_scenario_parquet(scenario_id, start, end, weather_df, pv_actual_kw, wind_actual_kw)
        _print_scenario_summary(scenario_id, df)


if __name__ == "__main__":
    main()
