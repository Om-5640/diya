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


# BUGFIX-2: priority order for picking the "locality" component of a short
# display name -- first match wins. Nominatim's addressdetails don't
# guarantee any single key is present (varies by place type/country), so
# this tries the most common locality-shaped keys before falling back to
# progressively broader ones.
_LOCALITY_KEYS = ("city", "town", "village", "suburb", "county", "state_district")


def _short_display_name(address: dict, fallback: str) -> str:
    """Compose a short "locality, region, country" name from Nominatim's
    addressdetails, e.g. "Bhuj, Gujarat, India" instead of the full
    multi-line display_name. Falls back to the full display_name if the
    address details don't have enough to build a shorter version from."""
    locality = next((address[k] for k in _LOCALITY_KEYS if address.get(k)), None)
    state = address.get("state")
    country = address.get("country")

    parts = [p for p in (locality, state, country) if p]
    if len(parts) < 2:
        return fallback
    # De-duplicate adjacent identical parts (e.g. a city that IS the state).
    deduped = [p for i, p in enumerate(parts) if i == 0 or p != parts[i - 1]]
    return ", ".join(deduped)


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
    """(lat, lon) -> {"display_name": str, "short_display_name": str}.

    short_display_name (BUGFIX-2) is a shorter "locality, region, country"
    composition (e.g. "Bhuj, Gujarat, India") built from Nominatim's own
    address components, for UI contexts where the full ~100+-character
    display_name gets awkwardly truncated. display_name's own meaning/format
    is unchanged; when address details aren't enough to build a shorter
    name, short_display_name just equals display_name.

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
            params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1},
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

    display_name = raw["display_name"]
    result = {
        "display_name": display_name,
        "short_display_name": _short_display_name(raw.get("address") or {}, display_name),
    }
    cache[key] = result
    return result
