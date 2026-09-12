"""
DIYA core/weather.py — historical and live weather fetching/caching for Khavda.

fetch_weather() pulls hourly weather from the Open-Meteo Archive API
(https://archive-api.open-meteo.com/v1/archive) for backtesting; caches raw
JSON under data/raw/ so the pipeline works fully offline once cached.

fetch_forecast_live() pulls from the separate Open-Meteo FORECAST API
(https://api.open-meteo.com/v1/forecast) for the /api/solve_live endpoint's
next-72h live plan; also caches (to data/raw/live_cache.json) so a later
call with no network can still fall back via load_cached_forecast().

No API key needed for either endpoint. UNIT CONVENTION: 1-hour timestep,
kW/kWh; power in kW.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
LIVE_CACHE_FILENAME = "live_cache.json"

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


def _parse_hourly_response(raw: dict) -> pd.DataFrame:
    """Shared Open-Meteo hourly-response parser (archive and forecast
    endpoints return the same `hourly` shape). Short gaps are interpolated;
    longer gaps raise ValueError -- used by fetch_weather, fetch_forecast_live,
    and load_cached_forecast alike."""
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


def slice_next_hours(df: pd.DataFrame, hours: int = 72) -> pd.DataFrame:
    """The `hours`-row window starting at the current hour (floored), in
    df's own tz. Falls back to the dataset's last `hours` rows if fewer
    than `hours` rows are available from "now" onward (e.g. a stale cache)
    -- always returns exactly `hours` rows when the dataset is long enough.
    """
    tz = df.index.tz
    now = pd.Timestamp.now(tz=tz).floor("h") if tz is not None else pd.Timestamp.now().floor("h")
    window = df.loc[df.index >= now]
    if len(window) < hours:
        window = df.tail(hours)
    return window.iloc[:hours]


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

    return _parse_hourly_response(raw)


def fetch_forecast_live(lat: float, lon: float, cache_dir: str | Path, forecast_days: int = 4) -> pd.DataFrame:
    """Fetch upcoming hourly weather from Open-Meteo's FORECAST endpoint
    (not the archive one) for (lat, lon), returning the next 72 hours from
    the current hour onward. On success, caches the raw JSON to
    <cache_dir>/live_cache.json.

    Raises on any network failure (connection error, timeout, non-200) --
    this function's only job is "get live data or fail clearly." Callers
    (see api/main.py's /api/solve_live) own the cached/scenario-replay
    fallback chain so a network outage never surfaces as a 500 to a demo.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / LIVE_CACHE_FILENAME

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "Asia/Kolkata",
        "wind_speed_unit": "ms",
        "forecast_days": forecast_days,
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=10)
    resp.raise_for_status()
    raw = resp.json()
    cache_path.write_text(json.dumps(raw), encoding="utf-8")

    df = _parse_hourly_response(raw)
    return slice_next_hours(df, hours=72)


def load_cached_forecast(cache_dir: str | Path) -> pd.DataFrame:
    """Read the last successfully cached live forecast. Raises
    FileNotFoundError if no cache exists yet (e.g. first-ever call with no
    network)."""
    cache_path = Path(cache_dir) / LIVE_CACHE_FILENAME
    if not cache_path.exists():
        raise FileNotFoundError(f"no live forecast cache at {cache_path}")
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    df = _parse_hourly_response(raw)
    return slice_next_hours(df, hours=72)
