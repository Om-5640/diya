"""
DIYA core/pv.py — PV power modeling.

Pipeline: solar position -> plane-of-array (POA) irradiance via isotropic
transposition -> cell temperature -> DC power, using pvlib. If any pvlib
call fails for any reason, falls back to a documented, dependency-free
inline isotropic transposition so pvlib issues never break the build (a
WARNING is logged when that happens). UNIT CONVENTION: 1-hour timestep,
kW/kWh; power in kW.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from core.types import PVSpec

logger = logging.getLogger(__name__)

ALBEDO = 0.2
NOCT_C = 45.0  # typical nominal operating cell temperature, degC


def _cell_temp_c(poa_global_w_m2: pd.Series, temp_air_c: pd.Series) -> pd.Series:
    """Simple NOCT cell-temperature model: T_cell = T_air + (NOCT-20)/800 * POA."""
    return temp_air_c + (NOCT_C - 20.0) / 800.0 * poa_global_w_m2


def pv_power_kw(
    weather_df: pd.DataFrame,
    pv_spec: PVSpec,
    lat: float,
    lon: float,
    elevation_m: float,
    tz: str,
) -> pd.Series:
    """AC-equivalent PV power (kW) for each hour in weather_df.

    weather_df must have tz-aware index and columns ghi, dni, dhi, temp_c
    (see core/weather.py). Clipped to [0, pv_spec.capacity_kwp].
    """
    index = weather_df.index
    if index.tz is None:
        index = index.tz_localize(tz)

    try:
        poa_global = _poa_global_pvlib(weather_df, index, pv_spec, lat, lon, elevation_m)
    except Exception:
        logger.warning(
            "pvlib POA computation failed; falling back to inline isotropic transposition",
            exc_info=True,
        )
        poa_global = _poa_global_fallback(weather_df, index, pv_spec, lat, lon)

    t_cell = _cell_temp_c(poa_global, weather_df["temp_c"])
    p_kw = (
        pv_spec.capacity_kwp
        * (poa_global / 1000.0)
        * (1 + pv_spec.temp_coeff_per_c * (t_cell - 25.0))
        * pv_spec.derate
    )
    p_kw = p_kw.clip(lower=0.0, upper=pv_spec.capacity_kwp)
    p_kw.name = "pv_kw"
    return p_kw


def _poa_global_pvlib(
    weather_df: pd.DataFrame,
    index: pd.DatetimeIndex,
    pv_spec: PVSpec,
    lat: float,
    lon: float,
    elevation_m: float,
) -> pd.Series:
    import pvlib

    solpos = pvlib.solarposition.get_solarposition(index, lat, lon, altitude=elevation_m)
    total_irrad = pvlib.irradiance.get_total_irradiance(
        surface_tilt=pv_spec.tilt_deg,
        surface_azimuth=pv_spec.azimuth_deg,
        solar_zenith=solpos["apparent_zenith"],
        solar_azimuth=solpos["azimuth"],
        dni=weather_df["dni"],
        ghi=weather_df["ghi"],
        dhi=weather_df["dhi"],
        model="isotropic",
    )
    return total_irrad["poa_global"]


def _poa_global_fallback(
    weather_df: pd.DataFrame,
    index: pd.DatetimeIndex,
    pv_spec: PVSpec,
    lat: float,
    lon: float,
) -> pd.Series:
    """Inline isotropic-sky transposition, no external dependency.

    Implements the classic Liu-Jordan isotropic diffuse-sky model (Duffie &
    Beckman, "Solar Engineering of Thermal Processes"):
        POA = DNI * max(cos(AOI), 0) + DHI*(1+cos(beta))/2 + GHI*albedo*(1-cos(beta))/2
    Solar position is approximated with Cooper's declination formula and the
    standard hour-angle equation referenced to IST (UTC+5:30, meridian
    82.5 deg E). This is not pvlib-grade precision — it exists purely to
    keep the pipeline running if pvlib is unavailable or errors out.
    """
    lat_r = math.radians(lat)
    beta_r = math.radians(pv_spec.tilt_deg)
    gamma_r = math.radians(pv_spec.azimuth_deg - 180.0)  # compass bearing -> south-referenced

    day_of_year = index.dayofyear.to_numpy()
    decl_r = np.radians(23.45 * np.sin(np.radians(360.0 / 365.0 * (284 + day_of_year))))

    b_r = np.radians(360.0 / 365.0 * (day_of_year - 81))
    eot_min = 9.87 * np.sin(2 * b_r) - 7.53 * np.cos(b_r) - 1.5 * np.sin(b_r)
    standard_meridian_deg = 82.5  # IST reference meridian
    local_hour = index.hour.to_numpy() + index.minute.to_numpy() / 60.0
    solar_time = local_hour + (4.0 * (lon - standard_meridian_deg) + eot_min) / 60.0
    hour_angle_r = np.radians(15.0 * (solar_time - 12.0))

    cos_zenith = np.sin(decl_r) * math.sin(lat_r) + np.cos(decl_r) * math.cos(lat_r) * np.cos(hour_angle_r)
    cos_zenith = np.clip(cos_zenith, -1.0, 1.0)

    cos_aoi = (
        np.sin(decl_r) * math.sin(lat_r) * math.cos(beta_r)
        - np.sin(decl_r) * math.cos(lat_r) * math.sin(beta_r) * math.cos(gamma_r)
        + np.cos(decl_r) * math.cos(lat_r) * math.cos(beta_r) * np.cos(hour_angle_r)
        + np.cos(decl_r) * math.sin(lat_r) * math.sin(beta_r) * math.cos(gamma_r) * np.cos(hour_angle_r)
        + np.cos(decl_r) * math.sin(beta_r) * math.sin(gamma_r) * np.sin(hour_angle_r)
    )
    cos_aoi = np.clip(cos_aoi, 0.0, 1.0)

    daytime = cos_zenith > 0.0
    dni = weather_df["dni"].to_numpy()
    dhi = weather_df["dhi"].to_numpy()
    ghi = weather_df["ghi"].to_numpy()

    poa_beam = np.where(daytime, dni * cos_aoi, 0.0)
    poa_sky_diffuse = dhi * (1 + math.cos(beta_r)) / 2.0
    poa_ground = ghi * ALBEDO * (1 - math.cos(beta_r)) / 2.0
    poa_global = np.clip(poa_beam + poa_sky_diffuse + poa_ground, 0.0, None)

    return pd.Series(poa_global, index=index, name="poa_global")
