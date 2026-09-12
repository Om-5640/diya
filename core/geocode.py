"""
DIYA core/geocode.py — server-side geocoding via OpenStreetMap Nominatim
(DIYA v2 Phase C.6).

Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
requires a real, identifying User-Agent and forbids heavy automated use
without caching -- both belong on the backend, never in browser JS calling
Nominatim directly. This module is the only place in DIYA that talks to it.

Never fabricates a lat/lon/display_name: a real "not found" or "service
unreachable" always surfaces as an exception, never a guessed value.
"""

from __future__ import annotations

import requests

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "DIYA/1.0 (contact: diya-project@example.com)"
REQUEST_TIMEOUT_S = 10


class GeocodeNotFoundError(Exception):
    """Nominatim reached successfully but returned no result."""


class GeocodeServiceError(Exception):
    """Nominatim itself was unreachable, timed out, or returned an error
    status -- never fabricate a result when this happens."""


def geocode_forward(query: str, cache: dict) -> dict:
    """Free-text query -> {"lat": float, "lon": float, "display_name": str}.

    `cache` is a plain dict the caller owns (no TTL -- fine for a demo);
    identical queries are served from it without a second Nominatim call.

    Raises GeocodeNotFoundError if Nominatim returns zero results,
    GeocodeServiceError if the request itself fails.
    """
    if query in cache:
        return cache[query]

    try:
        resp = requests.get(
            NOMINATIM_SEARCH_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        results = resp.json()
    except requests.RequestException as e:
        raise GeocodeServiceError(f"Nominatim forward geocoding request failed: {e}") from e

    if not results:
        raise GeocodeNotFoundError(f"no geocoding result for query={query!r}")

    result = {
        "lat": float(results[0]["lat"]),
        "lon": float(results[0]["lon"]),
        "display_name": results[0]["display_name"],
    }
    cache[query] = result
    return result


def geocode_reverse(lat: float, lon: float, cache: dict) -> dict:
    """(lat, lon) -> {"display_name": str}.

    `cache` is a plain dict the caller owns, keyed by (lat, lon) rounded to
    6 decimals (~0.1m precision) so trivially-different float noise doesn't
    defeat caching.

    Raises GeocodeNotFoundError if Nominatim returns no result,
    GeocodeServiceError if the request itself fails.
    """
    key = (round(lat, 6), round(lon, 6))
    if key in cache:
        return cache[key]

    try:
        resp = requests.get(
            NOMINATIM_REVERSE_URL,
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise GeocodeServiceError(f"Nominatim reverse geocoding request failed: {e}") from e

    # Nominatim's reverse endpoint returns HTTP 200 with an {"error": ...}
    # body (not a non-2xx status) when it has no result for the coordinates.
    if not raw or "error" in raw or "display_name" not in raw:
        raise GeocodeNotFoundError(f"no geocoding result for lat={lat}, lon={lon}")

    result = {"display_name": raw["display_name"]}
    cache[key] = result
    return result
