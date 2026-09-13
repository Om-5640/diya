"""
DIYA tests/test_bugfix2_multisite_weather.py — BUGFIX-2 regressions.

Two independent bugs, both traced to api.main._live_pv_wind_forecast's
fallback chain treating every site as interchangeable:

1. The live-forecast cache was a single fixed file (data/raw/live_cache.json)
   shared by every site, so a fallback for one site could silently serve
   another site's stale, wrong-location weather. Fixed by keying the cache
   file to (lat, lon) -- see core/weather.py's _live_cache_path.

2. The final "scenario replay" fallback (when both live fetch and cache
   fail) returned Khavda's own precomputed S2.parquet pv_fc_kw/wind_fc_kw
   verbatim for ANY site, bypassing that site's real wind.rated_kw entirely
   -- a site with zero wind hardware could show nonzero wind_kw. Fixed by
   replaying the REQUESTING site's own scenario data instead (falling back
   to honest zeros if it has none yet).

test_live_cache_isolated_per_coordinate is a deterministic, network-free
regression for bug 1 (fabricated weather payloads, no real HTTP). The other
tests exercise the real app end to end via TestClient, following the same
"real live network calls are acceptable in this suite" precedent already
set by tests/test_api_live.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from core import weather

client = TestClient(api_main.app)

GUJARAT = {"lat": 23.1878, "lon": 72.6293}  # DAIICT-equivalent
NYC = {"lat": 40.7128, "lon": -74.0060}


def _fake_hourly_json(signature: float) -> dict:
    """A minimal but structurally valid Open-Meteo hourly response spanning
    "now" (real wall-clock time), filled with one constant signature value
    per variable -- distinct signatures make cross-contamination between
    two coordinates trivially detectable."""
    now = pd.Timestamp.now(tz="Asia/Kolkata").floor("h") - pd.Timedelta(hours=6)
    times = pd.date_range(now, periods=96, freq="h", tz="Asia/Kolkata")
    return {
        "hourly": {
            "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
            "shortwave_radiation": [signature] * 96,
            "direct_normal_irradiance": [signature] * 96,
            "diffuse_radiation": [signature] * 96,
            "temperature_2m": [25.0] * 96,
            "wind_speed_10m": [signature] * 96,
            "cloud_cover": [10.0] * 96,
        },
        "timezone": "Asia/Kolkata",
    }


def test_live_cache_isolated_per_coordinate(tmp_path: Path):
    """BUGFIX-2 mechanism-level regression: writing a live-forecast cache
    for one coordinate must never be readable as another coordinate's
    cache, even when both files exist side by side in the same cache_dir."""
    lat_a, lon_a = GUJARAT["lat"], GUJARAT["lon"]
    lat_b, lon_b = NYC["lat"], NYC["lon"]

    path_a = weather._live_cache_path(tmp_path, lat_a, lon_a)
    path_b = weather._live_cache_path(tmp_path, lat_b, lon_b)
    assert path_a != path_b, "two far-apart coordinates must map to different cache files"

    path_a.write_text(json.dumps(_fake_hourly_json(111.0)), encoding="utf-8")
    path_b.write_text(json.dumps(_fake_hourly_json(222.0)), encoding="utf-8")

    df_a = weather.load_cached_forecast(lat_a, lon_a, cache_dir=tmp_path)
    df_b = weather.load_cached_forecast(lat_b, lon_b, cache_dir=tmp_path)

    assert (df_a["ghi"] == 111.0).all()
    assert (df_b["ghi"] == 222.0).all()

    # The historical bug in one sentence: site B's fallback used to read
    # whatever the LAST successful fetch (from any site) had written.
    assert not (df_a["ghi"] == df_b["ghi"]).all()


def test_load_cached_forecast_missing_for_this_coordinate_raises(tmp_path: Path):
    """A cache existing for one coordinate must not be treated as available
    for a different, uncached coordinate."""
    weather._live_cache_path(tmp_path, *GUJARAT.values()).write_text(
        json.dumps(_fake_hourly_json(1.0)), encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError):
        weather.load_cached_forecast(NYC["lat"], NYC["lon"], cache_dir=tmp_path)


def _wait_for_pipeline_complete(site_id: str, timeout_s: float = 240) -> dict:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/sites/{site_id}/pipeline_status").json()
        if status["status"] in ("complete", "failed"):
            return status
        time.sleep(0.5)
    raise AssertionError(f"pipeline_status for {site_id!r} did not finish within {timeout_s}s (last: {status})")


@pytest.fixture
def two_distant_sites():
    ids = []
    for label, coords in (("Gujarat", GUJARAT), ("NYC", NYC)):
        resp = client.post(
            "/api/sites",
            json={
                "display_name": f"Bugfix2 {label} site",
                "lat": coords["lat"],
                "lon": coords["lon"],
                "pv_capacity_kwp": 15.0,
                "battery_capacity_kwh": 30.0,
            },
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["site_id"])
    yield ids
    for site_id in ids:
        client.delete(f"/api/sites/{site_id}")


def test_live_dispatch_two_distant_sites_are_not_identical(two_distant_sites):
    """The phase's own acceptance test: two sites >1000km apart must not
    receive identical live-solve weather. Uses the real network (same
    precedent as test_api_live.py) -- if this ever flakes because both
    real fetches happen to produce identical arrays, that would itself be
    newsworthy, not just test noise."""
    gujarat_id, nyc_id = two_distant_sites

    resp_g = client.get(f"/api/sites/{gujarat_id}/dispatch", params={"range": 72, "source": "live"})
    resp_n = client.get(f"/api/sites/{nyc_id}/dispatch", params={"range": 72, "source": "live"})
    assert resp_g.status_code == 200
    assert resp_n.status_code == 200

    pv_g = [s["pv_kw"] for s in resp_g.json()["steps"]]
    pv_n = [s["pv_kw"] for s in resp_n.json()["steps"]]
    assert pv_g != pv_n, "two sites on opposite sides of the world must not get identical solar forecasts"


def test_scenario_replay_fallback_uses_requesting_sites_own_data_not_khavdas(monkeypatch, two_distant_sites):
    """Force both live fetch and cache to fail so _live_pv_wind_forecast
    lands on its last-resort scenario replay -- BUGFIX-2: this must replay
    the REQUESTING site's own scenario data, never Khavda's S2.parquet."""
    gujarat_id, _ = two_distant_sites
    status = _wait_for_pipeline_complete(gujarat_id)
    assert status["status"] == "complete", f"pipeline did not complete: {status}"

    scenarios_dir = Path("sites") / gujarat_id / "scenarios"
    assert (scenarios_dir / "S2.parquet").exists(), "test site should have its own S2.parquet by now"

    def _raise_network(*a, **k):
        raise RuntimeError("simulated network failure")

    def _raise_no_cache(*a, **k):
        raise FileNotFoundError("simulated: no cache present")

    monkeypatch.setattr(api_main, "fetch_forecast_live", _raise_network)
    monkeypatch.setattr(api_main, "load_cached_forecast", _raise_no_cache)

    resp = client.get(f"/api/sites/{gujarat_id}/dispatch", params={"range": 72, "source": "live"})
    assert resp.status_code == 200

    own_s2 = pd.read_parquet(scenarios_dir / "S2.parquet").iloc[:72]
    khavda_s2 = pd.read_parquet(Path("data") / "processed" / "S2.parquet").iloc[:72]
    fallback_wind = [s["wind_kw"] for s in resp.json()["steps"]]

    # This site has wind.rated_kw == 0.0 (default; not requested at
    # creation) -- its fallback wind_kw must be all zero, never Khavda's
    # own nonzero S2 wind_fc_kw values.
    assert all(w == 0.0 for w in fallback_wind), (
        f"a no-wind site's scenario-replay fallback returned nonzero wind_kw: {fallback_wind}"
    )
    assert list(own_s2["wind_fc_kw"]) == fallback_wind, "fallback must replay this site's OWN scenario data"
    assert not (khavda_s2["wind_fc_kw"] == 0).all(), "sanity check: khavda's own S2 wind is genuinely nonzero"


def test_wind_zero_enforced_in_every_live_step(two_distant_sites):
    """Section 3 regression: a site with wind.rated_kw == 0 must show
    wind_kw == 0.0 in EVERY step of a live response, not just spot-checked
    ones."""
    gujarat_id, nyc_id = two_distant_sites
    for site_id in (gujarat_id, nyc_id):
        resp = client.get(f"/api/sites/{site_id}/dispatch", params={"range": 72, "source": "live"})
        assert resp.status_code == 200
        wind_values = [s["wind_kw"] for s in resp.json()["steps"]]
        assert len(wind_values) == 72
        assert all(w == 0.0 for w in wind_values), f"site {site_id}: nonzero wind_kw for a no-wind site: {wind_values}"


def test_wind_zero_enforced_in_every_precomputed_step(two_distant_sites):
    """Section 3 regression, precomputed side: once this no-wind site's
    Phase E backfill has produced real scenario data, its precomputed
    dispatch must also show wind_kw == 0.0 in every step -- not just live."""
    gujarat_id, _ = two_distant_sites
    status = _wait_for_pipeline_complete(gujarat_id)
    assert status["status"] == "complete", f"pipeline did not complete: {status}"

    resp = client.get(f"/api/sites/{gujarat_id}/dispatch", params={"range": 168, "source": "precomputed"})
    assert resp.status_code == 200
    wind_values = [s["wind_kw"] for s in resp.json()["steps"]]
    assert len(wind_values) > 0
    assert all(w == 0.0 for w in wind_values), f"nonzero wind_kw in a no-wind site's precomputed run: {wind_values}"
