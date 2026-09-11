"""
DIYA core/weather.py — historical weather fetching and caching for Khavda.

Fetches hourly weather from the Open-Meteo Archive API
(https://archive-api.open-meteo.com/v1/archive, no API key required) and
caches the raw JSON response under data/raw/ so the pipeline works fully
offline once cached. UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY_VARS = [
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "temperature_2m",
    "wind_speed_10m",
    "cloud_cover",
]

COLUMN_MAP = {
    "shortwave_radiation": "ghi",
    "direct_normal_irradiance": "dni",
    "diffuse_radiation": "dhi",
    "temperature_2m": "temp_c",
    "wind_speed_10m": "wind_ms",
    "cloud_cover": "cloud_pct",
}

MAX_GAP_HOURS = 3


def _cache_path(cache_dir: str | Path, lat: float, lon: float, start_date: str, end_date: str) -> Path:
    return Path(cache_dir) / f"{lat}_{lon}_{start_date}_{end_date}.json"


def fetch_weather(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    cache_dir: str | Path,
) -> pd.DataFrame:
    """Fetch (or load from cache) hourly weather for [start_date, end_date].

    Always reads the cache when present — never re-fetches — so this
    function works offline once a given (lat, lon, start_date, end_date)
    has been cached once. Returns a DataFrame indexed by a tz-aware hourly
    DatetimeIndex (Asia/Kolkata) with columns: ghi, dni, dhi, temp_c,
    wind_ms, cloud_pct. Short data gaps (<= MAX_GAP_HOURS) are linearly
    interpolated; longer gaps raise ValueError.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, lat, lon, start_date, end_date)

    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "hourly": ",".join(HOURLY_VARS),
            "timezone": "Asia/Kolkata",
            "wind_speed_unit": "ms",
        }
        resp = requests.get(ARCHIVE_URL, params=params, timeout=120)
        resp.raise_for_status()
        raw = resp.json()
        path.write_text(json.dumps(raw), encoding="utf-8")

    hourly = raw["hourly"]
    df = pd.DataFrame({COLUMN_MAP[k]: hourly[k] for k in HOURLY_VARS})
    tz = raw.get("timezone", "Asia/Kolkata")
    df.index = pd.to_datetime(hourly["time"]).tz_localize(tz)
    df.index.name = "timestamp"

    df = df.interpolate(method="linear", limit=MAX_GAP_HOURS, limit_area="inside")
    if df.isna().any().any():
        bad_cols = df.columns[df.isna().any()].tolist()
        raise ValueError(f"weather data has gaps longer than {MAX_GAP_HOURS}h in columns: {bad_cols}")

    return df
