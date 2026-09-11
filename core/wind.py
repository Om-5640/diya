"""
DIYA core/wind.py — wind power from a simple cubic power curve, no external
library. UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.types import WindSpec


def wind_power_kw(weather_df: pd.DataFrame, wind_spec: WindSpec) -> pd.Series:
    """Wind power (kW) for each hour, from 10m wind speed extrapolated to
    hub height via a power-law shear profile, then a standard piecewise
    cubic power curve:
        0                                             v < cut_in
        rated_kw * (v**3 - cut_in**3) / (rated**3 - cut_in**3)   cut_in <= v < rated_ms
        rated_kw                                      rated_ms <= v < cut_out
        0                                              v >= cut_out
    """
    v_10m = weather_df["wind_ms"].to_numpy(dtype=float)
    v_hub = v_10m * (wind_spec.hub_height_m / 10.0) ** wind_spec.shear_exp

    cut_in = wind_spec.cut_in_ms
    rated_ms = wind_spec.rated_ms
    cut_out = wind_spec.cut_out_ms
    rated_kw = wind_spec.rated_kw

    p = np.zeros_like(v_hub)

    ramp_mask = (v_hub >= cut_in) & (v_hub < rated_ms)
    denom = rated_ms**3 - cut_in**3
    if denom > 0:
        p[ramp_mask] = rated_kw * (v_hub[ramp_mask] ** 3 - cut_in**3) / denom

    flat_mask = (v_hub >= rated_ms) & (v_hub < cut_out)
    p[flat_mask] = rated_kw

    return pd.Series(p, index=weather_df.index, name="wind_kw")
