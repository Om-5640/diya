"""
DIYA tests/test_dataflow.py — Phase 1A data-layer tests: wind curve, PV
day/night behavior, deterministic load generation, forecast error growth,
and the three scenario parquet files produced by scripts/build_scenarios.py.

No network calls — weather inputs are synthetic DataFrames built in-process.
These tests assume scripts/build_scenarios.py has already been run once to
produce data/processed/{S1,S2,S3}.parquet (see README / acceptance flow).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.forecast import make_forecast
from core.load import generate_load
from core.pv import pv_power_kw
from core.types import PVSpec, WindSpec
from core.wind import wind_power_kw

REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

EXPECTED_PARQUET_COLUMNS = {
    "timestamp",
    "ghi",
    "dni",
    "dhi",
    "temp_c",
    "wind_ms",
    "pv_actual_kw",
    "pv_fc_kw",
    "wind_actual_kw",
    "wind_fc_kw",
    "load_critical_kw",
    "load_essential_kw",
    "load_deferrable_kw",
}


# ---------------------------------------------------------------------------
# wind
# ---------------------------------------------------------------------------


def _test_wind_spec() -> WindSpec:
    # hub_height_m == 10 so the shear adjustment is a no-op (multiplier 1.0),
    # which makes the cut_in/rated/cut_out boundary checks exact below.
    return WindSpec(rated_kw=10, hub_height_m=10, cut_in_ms=3.0, rated_ms=11.0, cut_out_ms=20.0, shear_exp=0.14)


def test_wind_power_curve_boundaries_and_monotonic() -> None:
    spec = _test_wind_spec()
    speeds = np.arange(0.0, 25.5, 0.5)
    idx = pd.date_range("2025-06-01", periods=len(speeds), freq="h", tz="Asia/Kolkata")
    weather_df = pd.DataFrame({"wind_ms": speeds}, index=idx)

    p = wind_power_kw(weather_df, spec)

    assert (p[speeds < spec.cut_in_ms] == 0).all()
    assert (p[speeds >= spec.cut_out_ms] == 0).all()

    at_rated = p[np.isclose(speeds, spec.rated_ms)]
    assert len(at_rated) > 0
    assert np.allclose(at_rated.to_numpy(), spec.rated_kw)

    ramp_mask = (speeds >= spec.cut_in_ms) & (speeds < spec.rated_ms)
    ramp_values = p[ramp_mask].to_numpy()
    assert np.all(np.diff(ramp_values) >= -1e-9)


# ---------------------------------------------------------------------------
# pv
# ---------------------------------------------------------------------------


def _clear_june_day_weather() -> pd.DataFrame:
    idx = pd.date_range("2025-06-15 00:00", periods=24, freq="h", tz="Asia/Kolkata")
    hours = idx.hour.to_numpy()
    ghi = np.where((hours >= 6) & (hours <= 18), np.maximum(0.0, 900.0 * np.sin(np.pi * (hours - 6) / 12)), 0.0)
    dni = ghi * 0.8
    dhi = ghi * 0.2
    return pd.DataFrame(
        {
            "ghi": ghi,
            "dni": dni,
            "dhi": dhi,
            "temp_c": 35.0,
            "wind_ms": 3.0,
            "cloud_pct": 10.0,
        },
        index=idx,
    )


def test_pv_power_zero_at_midnight_positive_at_noon() -> None:
    weather_df = _clear_june_day_weather()
    pv_spec = PVSpec(capacity_kwp=60, tilt_deg=24, azimuth_deg=180, temp_coeff_per_c=-0.004, derate=0.90)

    p_kw = pv_power_kw(weather_df, pv_spec, lat=23.8443, lon=69.7317, elevation_m=15, tz="Asia/Kolkata")

    midnight = p_kw.iloc[0]
    noon = p_kw[weather_df.index.hour == 12].iloc[0]

    assert midnight == 0.0
    assert noon > 0.0
    assert noon <= pv_spec.capacity_kwp


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


def test_generate_load_deterministic_and_critical_never_zero() -> None:
    idx = pd.date_range("2025-01-06", periods=168, freq="h", tz="Asia/Kolkata")  # a Monday

    df1 = generate_load(idx, seed=42, load_multiplier=1.0)
    df2 = generate_load(idx, seed=42, load_multiplier=1.0)
    pd.testing.assert_frame_equal(df1, df2)

    assert (df1["critical_kw"] > 0).all()


# ---------------------------------------------------------------------------
# forecast
# ---------------------------------------------------------------------------


def _synthetic_pv_actual(n: int = 168) -> pd.Series:
    idx = pd.date_range("2025-01-06", periods=n, freq="h", tz="Asia/Kolkata")
    hours = idx.hour.to_numpy()
    values = np.maximum(0.0, 45.0 * np.sin(np.pi * (hours - 6) / 12))
    values = np.where((hours >= 6) & (hours <= 18), values, 0.0)
    return pd.Series(values, index=idx, name="pv_kw")


def test_make_forecast_error_grows_with_lead_time_and_respects_bounds() -> None:
    actual = _synthetic_pv_actual()

    fc_short = make_forecast(actual, seed=7, lead_hours=6)
    fc_long = make_forecast(actual, seed=7, lead_hours=48)

    mae_short = float(np.mean(np.abs(fc_short.to_numpy() - actual.to_numpy())))
    mae_long = float(np.mean(np.abs(fc_long.to_numpy() - actual.to_numpy())))

    assert mae_short > 0
    assert mae_long > mae_short

    for fc in (fc_short, fc_long):
        assert (fc >= 0).all()
        assert (fc <= actual.max() + 1e-9).all()


# ---------------------------------------------------------------------------
# scenario parquet files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_id", ["S1", "S2", "S3"])
def test_scenario_parquet_exists_and_has_expected_schema(scenario_id: str) -> None:
    path = PROCESSED_DIR / f"{scenario_id}.parquet"
    assert path.exists(), f"missing {path}; run scripts/build_scenarios.py first"

    df = pd.read_parquet(path)
    assert set(df.columns) == EXPECTED_PARQUET_COLUMNS
    assert len(df) == 168
