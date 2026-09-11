"""
DIYA core/forecast.py — forecast-at-decision-time generator + error metrics.

CRITICAL RULE: the optimizer may ONLY ever see the output of make_forecast.
The realized/actual series is for the simulator alone. Any code path that
passes realized weather, realized PV power, or realized wind power into the
optimizer is a bug — with the sole, explicitly-named exception of the
perfect_foresight policy.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

AR1_PHI = 0.85
SIGMA0_SOLAR = 0.18
SIGMA0_WIND = 0.25


def _base_sigma0(series: pd.Series) -> float:
    name = (series.name or "") if series.name is not None else ""
    return SIGMA0_WIND if "wind" in str(name).lower() else SIGMA0_SOLAR


def make_forecast(
    actual_series: pd.Series,
    seed: int,
    lead_hours: int = 24,
    bias: float = 0.0,
    noise_scale: float = 1.0,
) -> pd.Series:
    """Simulate the forecast that would have been available `lead_hours`
    before each timestamp, given the realized `actual_series`.

    Error model: multiplicative, forecast_t = actual_t * (1 + bias + e_t),
    where e_t follows an AR(1) process (phi=0.85) so forecast errors are
    correlated hour-to-hour rather than iid, with stationary standard
    deviation sigma(lead_hours) = sigma0 * sqrt(lead_hours / 24). sigma0 is
    0.25 if `actual_series.name` contains "wind" (case-insensitive), else
    0.18 (solar default) — both then scaled by `noise_scale`. `bias`
    represents an optional systematic cloud-forecast bias. Fully
    deterministic given `seed`. Output is clipped to
    [0, actual_series.max()] (physical bounds).
    """
    rng = np.random.default_rng(seed)
    n = len(actual_series)

    sigma0 = _base_sigma0(actual_series) * noise_scale
    sigma_target = sigma0 * math.sqrt(max(lead_hours, 1e-6) / 24.0)
    sigma_innov = sigma_target * math.sqrt(max(1.0 - AR1_PHI**2, 1e-9))

    innovations = rng.normal(0.0, sigma_innov, size=n)
    error = np.empty(n)
    if n > 0:
        error[0] = innovations[0]
        for i in range(1, n):
            error[i] = AR1_PHI * error[i - 1] + innovations[i]

    actual = actual_series.to_numpy(dtype=float)
    forecast = actual * (1.0 + bias + error)

    upper_bound = float(actual_series.max()) if n > 0 else 0.0
    forecast = np.clip(forecast, 0.0, upper_bound if upper_bound > 0 else None)

    return pd.Series(forecast, index=actual_series.index, name=f"{actual_series.name}_fc")


def forecast_error_metrics(forecast: pd.Series, actual: pd.Series) -> dict:
    """Error metrics for `forecast` against realized `actual`.

    Returns MAE, RMSE, bias (mean signed error) and nRMSE (RMSE normalized
    by the mean of `actual`), both overall and broken out by lead-time
    bucket. Lead-time buckets assume `forecast` represents a rolling,
    once-daily (24h-cycle) forecast: position i within each 24-hour cycle
    is treated as an (i % 24 + 1)-hour-ahead forecast, bucketed into the
    1-6h / 7-12h / 13-18h / 19-24h quartiles of that day-ahead cycle.
    """
    err = forecast.to_numpy(dtype=float) - actual.to_numpy(dtype=float)
    actual_np = actual.to_numpy(dtype=float)

    def _metrics(e: np.ndarray, a: np.ndarray) -> dict:
        a_mean = float(np.mean(a)) if len(a) else 0.0
        mae = float(np.mean(np.abs(e))) if len(e) else float("nan")
        rmse = float(np.sqrt(np.mean(e**2))) if len(e) else float("nan")
        bias = float(np.mean(e)) if len(e) else float("nan")
        nrmse = rmse / a_mean if a_mean > 0 else float("nan")
        return {"mae": mae, "rmse": rmse, "bias": bias, "nrmse": nrmse}

    overall = _metrics(err, actual_np)

    n = len(forecast)
    lead = (np.arange(n) % 24) + 1
    bucket_ranges = {
        "1-6h": (1, 6),
        "7-12h": (7, 12),
        "13-18h": (13, 18),
        "19-24h": (19, 24),
    }
    by_bucket = {}
    for label, (lo, hi) in bucket_ranges.items():
        mask = (lead >= lo) & (lead <= hi)
        if mask.sum() == 0:
            continue
        by_bucket[label] = _metrics(err[mask], actual_np[mask])

    return {"overall": overall, "by_lead_bucket": by_bucket}
