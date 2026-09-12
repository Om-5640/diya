"""
DIYA scripts/generate_api_contract.py — regenerates the API contract docs
FROM the real, running FastAPI app (never hand-typed): docs/openapi.json
(the raw schema from app.openapi()) and docs/API_CONTRACT.md (a
human-readable rendering with real, captured example request/response
pairs).

Re-run this any time api/ changes -- the contract is generated, not
maintained by hand, so it can never silently drift from the real code.

Examples are captured by ACTUALLY CALLING the routes in-process via
FastAPI's TestClient against real data: khavda for read routes, a
throwaway test site (created, exercised, then deleted) for write routes.
Nothing in this doc is guessed or hand-typed.

NOTE: this script calls the real POST /api/resolve once (khavda scenario
S1), which is the genuinely slow ~60-65s rolling-MPC re-solve documented in
api/main.py and FREEZE_NOTES.md -- expect this script to take about a
minute to run, not seconds.

DIYA v2 Phase E: creating the throwaway site also enqueues a real
365-day build+precompute background job (core/pipeline_worker.py) -- this
script does not fast-mode it (unlike the test suite's tests/conftest.py),
so its pipeline_status example is captured mid-flight, not waited out to
completion. The throwaway site is deleted at the end of this function
while that job may still be running in the background; the worker thread
dies with the process when this script exits, same as any other
short-lived script run.

GEOCODE EXAMPLES ARE THE ONE EXCEPTION TO "REAL NETWORK CALL": Nominatim
returns 403 Forbidden from several sandboxed/CI network environments
(observed directly while building Phase C.6 -- not a code bug, core/geocode.py
correctly turns it into a 502), so a truly live call here would sometimes
capture a network-block error instead of a representative success example.
core.geocode.requests.get is mocked ONLY for this script's two geocode
example captures (same technique tests/test_convenience_endpoints.py already
uses for its geocode tests) -- every other example on every other route in
this script is still a real, unmocked call through the real code path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import api.main as api_main  # noqa: E402

DOCS_DIR = REPO_ROOT / "docs"
OPENAPI_PATH = DOCS_DIR / "openapi.json"
CONTRACT_PATH = DOCS_DIR / "API_CONTRACT.md"

client = TestClient(api_main.app)

# Explicit route order + grouping for the rendered doc. Every path FastAPI
# actually serves must appear here exactly once -- the script asserts this
# at the end so a route added to api/main.py without a doc entry fails
# loudly instead of silently missing from the contract.
LEGACY_ROUTES: list[tuple[str, str]] = [
    ("GET", "/"),
    ("GET", "/api/health"),
    ("GET", "/api/runs"),
    ("GET", "/api/runs/{scenario_id}/{policy}"),
    ("POST", "/api/solve_live"),
    ("POST", "/api/resolve"),
]
SITE_SCOPED_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/sites"),
    ("POST", "/api/sites"),
    ("GET", "/api/sites/{site_id}"),
    ("PATCH", "/api/sites/{site_id}"),
    ("DELETE", "/api/sites/{site_id}"),
    ("GET", "/api/sites/{site_id}/pipeline_status"),
    ("POST", "/api/sites/{site_id}/rebuild"),
    ("GET", "/api/sites/{site_id}/scenarios"),
    ("GET", "/api/sites/{site_id}/runs"),
    ("GET", "/api/sites/{site_id}/runs/{scenario_id}/{policy}"),
    ("POST", "/api/sites/{site_id}/solve_live"),
    ("POST", "/api/sites/{site_id}/resolve"),
]
# Phase C.6: aggregation/convenience endpoints. Grouped separately from the
# two lists above since geocode/methodology aren't site-scoped at all, and
# overview/dispatch/evidence/export -- while site-scoped in URL shape -- are
# a distinct "compose existing results" layer on top of everything else.
CONVENIENCE_ROUTES: list[tuple[str, str]] = [
    ("POST", "/api/geocode"),
    ("POST", "/api/geocode/reverse"),
    ("GET", "/api/sites/{site_id}/overview"),
    ("GET", "/api/sites/{site_id}/dispatch"),
    ("GET", "/api/sites/{site_id}/evidence"),
    ("GET", "/api/sites/{site_id}/export"),
    ("GET", "/api/methodology"),
]

KHAVDA_LIVE_BODY = {"lat": 23.8443, "lon": 69.7317, "soc_pct": 60.0}
KHAVDA_RESOLVE_BODY = {
    "scenario_id": "S1",
    "weights": {"cost": 1.0, "co2": 1.0, "reliability": 1.0},
    "k_uncertainty": 1.0,
    "diesel_price_inr_per_l": 92.5,
}
NEW_SITE_BODY = {
    "display_name": "API Contract Example Site",
    "lat": -33.9,
    "lon": 18.4,
    "pv_capacity_kwp": 10.0,
    "battery_capacity_kwh": 30.0,
}


def _safe_json(resp) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


Example = dict  # {"description", "request", "path", "status", "response", "is_text"}


def _capture_examples() -> dict[tuple[str, str], list[Example]]:
    """Drives the real app via TestClient and records real request/response
    pairs, keyed by (method, path_template). Read routes use khavda's real,
    already-committed data. Write routes use one throwaway site, created
    here and deleted again before this function returns."""
    examples: dict[tuple[str, str], list[Example]] = {}

    def record(method: str, template: str, path: str, resp, req_body=None, desc: str = "") -> None:
        content_type = resp.headers.get("content-type", "")
        is_text = not content_type.startswith("application/json")
        response_payload = resp.text if is_text else _safe_json(resp)
        examples.setdefault((method, template), []).append(
            {
                "description": desc,
                "path": path,
                "request": req_body,
                "status": resp.status_code,
                "response": response_payload,
                "is_text": is_text,
            }
        )

    # --- Legacy / global ---
    record("GET", "/", "/", client.get("/"), desc="Service pointer")
    record("GET", "/api/health", "/api/health", client.get("/api/health"), desc="Liveness check")

    record("GET", "/api/runs", "/api/runs", client.get("/api/runs"), desc="All precomputed Khavda runs")

    record(
        "GET",
        "/api/runs/{scenario_id}/{policy}",
        "/api/runs/S2/mpc",
        client.get("/api/runs/S2/mpc"),
        desc="Existing precomputed run",
    )
    record(
        "GET",
        "/api/runs/{scenario_id}/{policy}",
        "/api/runs/S2/does_not_exist",
        client.get("/api/runs/S2/does_not_exist"),
        desc="Unknown policy -> 404",
    )

    record(
        "POST",
        "/api/solve_live",
        "/api/solve_live",
        client.post("/api/solve_live", json=KHAVDA_LIVE_BODY),
        req_body=KHAVDA_LIVE_BODY,
        desc="Live 72h forecast-driven plan for Khavda's coordinates",
    )

    # SLOW: the real ~60-65s rolling MPC re-solve, captured once, for real.
    record(
        "POST",
        "/api/resolve",
        "/api/resolve",
        client.post("/api/resolve", json=KHAVDA_RESOLVE_BODY),
        req_body=KHAVDA_RESOLVE_BODY,
        desc="Re-solve Khavda scenario S1 with default weights (this is the slow ~60-65s endpoint)",
    )
    bad_resolve_body = {**KHAVDA_RESOLVE_BODY, "scenario_id": "does_not_exist"}
    record(
        "POST",
        "/api/resolve",
        "/api/resolve",
        client.post("/api/resolve", json=bad_resolve_body),
        req_body=bad_resolve_body,
        desc="Unknown scenario_id -> 404",
    )

    # --- Site-scoped: khavda reads (safe, no mutation) ---
    record("GET", "/api/sites", "/api/sites", client.get("/api/sites"), desc="Every registered site")

    record("GET", "/api/sites/{site_id}", "/api/sites/khavda", client.get("/api/sites/khavda"), desc="Khavda's summary")
    record(
        "GET",
        "/api/sites/{site_id}",
        "/api/sites/does_not_exist",
        client.get("/api/sites/does_not_exist"),
        desc="Unknown site_id -> 404",
    )

    record(
        "GET",
        "/api/sites/{site_id}/scenarios",
        "/api/sites/khavda/scenarios",
        client.get("/api/sites/khavda/scenarios"),
        desc="Khavda's named scenarios",
    )

    record(
        "GET",
        "/api/sites/{site_id}/runs",
        "/api/sites/khavda/runs",
        client.get("/api/sites/khavda/runs"),
        desc="Khavda's precomputed runs, via the site-scoped route",
    )
    record(
        "GET",
        "/api/sites/{site_id}/runs/{scenario_id}/{policy}",
        "/api/sites/khavda/runs/S2/mpc",
        client.get("/api/sites/khavda/runs/S2/mpc"),
        desc="Existing precomputed run, via the site-scoped route",
    )

    record(
        "DELETE",
        "/api/sites/{site_id}",
        "/api/sites/khavda",
        client.delete("/api/sites/khavda"),
        desc="Seed sites can never be deleted -> 400",
    )

    record(
        "GET",
        "/api/sites/{site_id}/pipeline_status",
        "/api/sites/khavda/pipeline_status",
        client.get("/api/sites/khavda/pipeline_status"),
        desc="Khavda: always complete, no job ever runs for a seed site",
    )
    record(
        "POST",
        "/api/sites/{site_id}/rebuild",
        "/api/sites/khavda/rebuild",
        client.post("/api/sites/khavda/rebuild"),
        desc="Seed sites can never be rebuilt through this API -> 400",
    )

    # --- Site-scoped: write routes on one throwaway test site ---
    create_resp = client.post("/api/sites", json=NEW_SITE_BODY)
    record("POST", "/api/sites", "/api/sites", create_resp, req_body=NEW_SITE_BODY, desc="Register a new site")
    site_id = create_resp.json()["site_id"]

    # DIYA v2 Phase E: creating a site enqueues a real background
    # build+precompute job immediately (this script does not fast-mode it,
    # unlike the test suite -- it's the same real 365-day default a genuine
    # new site gets). Captured here at its natural, still-in-progress state,
    # the same honest-capture spirit as the "no precomputed runs yet" 404
    # right below -- not waited out to completion (that would make this
    # script take as long as a real historical build, not seconds).
    record(
        "GET",
        "/api/sites/{site_id}/pipeline_status",
        f"/api/sites/{site_id}/pipeline_status",
        client.get(f"/api/sites/{site_id}/pipeline_status"),
        desc="A brand-new site's pipeline job shortly after creation",
    )
    record(
        "POST",
        "/api/sites/{site_id}/rebuild",
        f"/api/sites/{site_id}/rebuild",
        client.post(f"/api/sites/{site_id}/rebuild"),
        desc="Re-enqueuing while a job is already in progress returns the SAME job, not a duplicate",
    )

    record(
        "GET",
        "/api/sites/{site_id}/runs/{scenario_id}/{policy}",
        f"/api/sites/{site_id}/runs/S1/mpc",
        client.get(f"/api/sites/{site_id}/runs/S1/mpc"),
        desc="A brand-new site has no precomputed runs yet -> 404",
    )

    patch_body = {
        "economics": {
            "diesel_price_inr_per_l": 100.0,
            "diesel_price_source": "manual override (example)",
            "co2_price_inr_per_kg": 2.0,
            "voll_critical_inr_per_kwh": 500.0,
            "voll_essential_inr_per_kwh": 60.0,
            "voll_deferrable_inr_per_kwh": 12.0,
        }
    }
    record(
        "PATCH",
        "/api/sites/{site_id}",
        f"/api/sites/{site_id}",
        client.patch(f"/api/sites/{site_id}", json=patch_body),
        req_body=patch_body,
        desc="Partial economics update on a non-seed site",
    )

    new_site_live_body = {"lat": NEW_SITE_BODY["lat"], "lon": NEW_SITE_BODY["lon"], "soc_pct": 50.0}
    record(
        "POST",
        "/api/sites/{site_id}/solve_live",
        f"/api/sites/{site_id}/solve_live",
        client.post(f"/api/sites/{site_id}/solve_live", json=new_site_live_body),
        req_body=new_site_live_body,
        desc="Live 72h plan for the new site's own hardware/coordinates",
    )

    record(
        "POST",
        "/api/sites/{site_id}/resolve",
        f"/api/sites/{site_id}/resolve",
        client.post(f"/api/sites/{site_id}/resolve", json=KHAVDA_RESOLVE_BODY),
        req_body=KHAVDA_RESOLVE_BODY,
        desc="A brand-new site has no scenario data yet -> 404 (fast, no MILP solve)",
    )

    # Clean up: never leave the throwaway site behind.
    del_resp = client.delete(f"/api/sites/{site_id}")
    record("DELETE", "/api/sites/{site_id}", f"/api/sites/{site_id}", del_resp, desc="Delete a non-seed site -> 204")

    # --- Phase C.6: convenience endpoints ---

    # Nominatim itself is mocked ONLY here -- see this script's module
    # docstring (GEOCODE EXAMPLES...) for why.
    fake_forward_resp = MagicMock()
    fake_forward_resp.raise_for_status = lambda: None
    fake_forward_resp.json = lambda: [{"lat": "23.2419", "lon": "69.6669", "display_name": "Bhuj, Kutch, Gujarat, India"}]
    with patch("core.geocode.requests.get", return_value=fake_forward_resp):
        forward_body = {"query": "Bhuj, Gujarat"}
        record(
            "POST", "/api/geocode", "/api/geocode",
            client.post("/api/geocode", json=forward_body),
            req_body=forward_body,
            desc="Forward geocode a place name (Nominatim mocked for this capture only -- see script docstring)",
        )

    fake_reverse_resp = MagicMock()
    fake_reverse_resp.raise_for_status = lambda: None
    fake_reverse_resp.json = lambda: {"display_name": "Bhuj, Kutch, Gujarat, India"}
    with patch("core.geocode.requests.get", return_value=fake_reverse_resp):
        reverse_body = {"lat": 23.2419, "lon": 69.6669}
        record(
            "POST", "/api/geocode/reverse", "/api/geocode/reverse",
            client.post("/api/geocode/reverse", json=reverse_body),
            req_body=reverse_body,
            desc="Reverse geocode coordinates (Nominatim mocked for this capture only -- see script docstring)",
        )

    # overview / dispatch / evidence / export -- khavda (has precomputed runs)
    record(
        "GET", "/api/sites/{site_id}/overview", "/api/sites/khavda/overview",
        client.get("/api/sites/khavda/overview"),
        desc="Khavda: status=full (has precomputed runs)",
    )
    record(
        "GET", "/api/sites/{site_id}/dispatch", "/api/sites/khavda/dispatch?range=72",
        client.get("/api/sites/khavda/dispatch?range=72"),
        desc="Khavda: precomputed source, truncated to 72h",
    )
    record(
        "GET", "/api/sites/{site_id}/evidence", "/api/sites/khavda/evidence?scenario=S2",
        client.get("/api/sites/khavda/evidence?scenario=S2"),
        desc="Khavda: 4-policy comparison + server-computed delta for scenario S2",
    )
    record(
        "GET", "/api/sites/{site_id}/export", "/api/sites/khavda/export?scenario=S2&policy=mpc&format=csv",
        client.get("/api/sites/khavda/export?scenario=S2&policy=mpc&format=csv"),
        desc="Khavda: CSV export of S2/mpc's steps",
    )
    record(
        "GET", "/api/sites/{site_id}/export", "/api/sites/khavda/export?scenario=S2&policy=mpc&format=json",
        client.get("/api/sites/khavda/export?scenario=S2&policy=mpc&format=json"),
        desc="Khavda: JSON export of the same run",
    )

    # A second throwaway site (the first, created earlier, is already
    # deleted) to demonstrate overview/dispatch's live-only / live-fallback
    # behavior for a site with no precomputed runs yet.
    create_resp2 = client.post("/api/sites", json=NEW_SITE_BODY)
    site_id2 = create_resp2.json()["site_id"]

    record(
        "GET", "/api/sites/{site_id}/overview", f"/api/sites/{site_id2}/overview",
        client.get(f"/api/sites/{site_id2}/overview"),
        desc="A brand-new site: status=live_only (no precomputed runs yet)",
    )
    record(
        "GET", "/api/sites/{site_id}/dispatch", f"/api/sites/{site_id2}/dispatch?range=72",
        client.get(f"/api/sites/{site_id2}/dispatch?range=72"),
        desc="A brand-new site: falls back to a live solve for range<=72",
    )
    record(
        "GET", "/api/sites/{site_id}/dispatch", f"/api/sites/{site_id2}/dispatch?range=168",
        client.get(f"/api/sites/{site_id2}/dispatch?range=168"),
        desc="A brand-new site: range=168 refused rather than fabricated -> 404",
    )

    client.delete(f"/api/sites/{site_id2}")

    record(
        "GET", "/api/methodology", "/api/methodology",
        client.get("/api/methodology"),
        desc="DIYA's general data-provenance methodology (fixed, not site-specific)",
    )

    return examples


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _resolve(schema: dict, components: dict) -> dict:
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return components["schemas"][name]
    return schema


def _type_str(field_schema: dict, components: dict) -> str:
    if "$ref" in field_schema:
        return field_schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in field_schema:
        return " | ".join(_type_str(s, components) for s in field_schema["anyOf"])
    t = field_schema.get("type", "any")
    if t == "array":
        return f"list[{_type_str(field_schema.get('items', {}), components)}]"
    return t


def _render_schema_fields(schema_ref: dict, components: dict) -> list[str]:
    schema = _resolve(schema_ref, components)
    if schema.get("type") == "array":
        inner = _resolve(schema.get("items", {}), components)
        lines = [f"Array of `{schema.get('items', {}).get('$ref', 'object').rsplit('/', 1)[-1]}`:"]
        lines += _render_schema_fields(schema.get("items", {}), components)
        return lines
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not props:
        return ["*(no fields)*"]
    lines = []
    for name, field_schema in props.items():
        type_str = _type_str(field_schema, components)
        req_str = "required" if name in required else "optional"
        default = field_schema.get("default", None)
        default_str = f", default={default!r}" if default is not None else ""
        lines.append(f"- `{name}`: {type_str} ({req_str}{default_str})")
    return lines


def _is_step_like(item: Any) -> bool:
    return isinstance(item, dict) and "t" in item and "reason_code" in item


def _truncate_steps(payload: Any) -> Any:
    """RunResult-shaped payloads (and anything embedding one, like
    /evidence's four runs, /dispatch's steps array, or /export's bare JSON
    steps list) carry up to 168 hourly steps -- truncate to the first 2 for
    readability in the doc, noting the real total. Recurses into dict
    values so nested RunResults (evidence's rule_based/mpc/diesel_only/
    perfect_foresight) get the same treatment."""
    if isinstance(payload, list) and len(payload) > 3 and all(_is_step_like(item) for item in payload):
        return payload[:2] + [f"... ({len(payload)} steps total, truncated for this doc)"]
    if isinstance(payload, dict):
        out = dict(payload)
        if "steps" in out and isinstance(out["steps"], list) and len(out["steps"]) > 3:
            out["steps"] = out["steps"][:2] + [f"... ({len(out['steps'])} steps total, truncated for this doc)"]
        for key, value in out.items():
            if key != "steps" and isinstance(value, (dict, list)):
                out[key] = _truncate_steps(value)
        return out
    return payload


def _render_example(ex: Example) -> str:
    lines = [f"*{ex['description']}*" if ex["description"] else "", f"- Path: `{ex['path']}`"]
    if ex["request"] is not None:
        lines.append("- Request body:")
        lines.append("```json")
        lines.append(json.dumps(ex["request"], indent=2))
        lines.append("```")
    lines.append(f"- Response ({ex['status']}):")
    if ex.get("is_text"):
        lines.append("```")
        text = ex["response"] or ""
        text_lines = text.splitlines()
        if len(text_lines) > 5:
            text = "\n".join(text_lines[:5] + [f"... ({len(text_lines)} lines total, truncated for this doc)"])
        lines.append(text)
        lines.append("```")
    else:
        lines.append("```json")
        lines.append(json.dumps(_truncate_steps(ex["response"]), indent=2))
        lines.append("```")
    return "\n".join(line for line in lines if line != "")


def _render_route(method: str, path: str, schema: dict, examples: list[Example]) -> str:
    op = schema["paths"][path][method.lower()]
    description = op.get("description", "").strip() or "*(no description)*"
    components = schema.get("components", {})

    out = [f"### `{method} {path}`", "", description, ""]

    params = op.get("parameters", [])
    if params:
        out.append("**Path/query parameters:**")
        for p in params:
            out.append(f"- `{p['name']}` ({p['in']}): {p['schema'].get('type', 'any')}{' — required' if p.get('required') else ''}")
        out.append("")

    if "requestBody" in op:
        req_schema_ref = op["requestBody"]["content"]["application/json"]["schema"]
        out.append("**Request body schema:**")
        out.extend(_render_schema_fields(req_schema_ref, components))
        out.append("")

    success_codes = [c for c in op["responses"] if c.startswith("2")]
    for code in success_codes:
        resp_entry = op["responses"][code]
        out.append(f"**Response schema ({code}):**")
        if "content" in resp_entry:
            resp_schema_ref = resp_entry["content"]["application/json"]["schema"]
            out.extend(_render_schema_fields(resp_schema_ref, components))
        else:
            out.append("*(no content)*")
        out.append("")

    error_codes = [c for c in op["responses"] if not c.startswith("2")]
    if error_codes:
        out.append(f"**Possible status codes beyond 200:** {', '.join(error_codes)} (422 = request body/params failed validation; see the description above for endpoint-specific error meanings).")
        out.append("")

    if examples:
        out.append(f"**Example{'s' if len(examples) > 1 else ''}:**")
        out.append("")
        for ex in examples:
            out.append(_render_example(ex))
            out.append("")

    return "\n".join(out)


def _render_markdown(schema: dict, examples: dict[tuple[str, str], list[Example]]) -> str:
    lines = [
        "# DIYA API Contract",
        "",
        "Generated by scripts/generate_api_contract.py -- do not hand-edit; "
        "re-run the script after any api/ change.",
        "",
        f"OpenAPI version: {schema.get('openapi')}. API version: {schema.get('info', {}).get('version')}.",
        "Raw machine-readable schema: [openapi.json](openapi.json). Interactive docs: `/docs` on a running server.",
        "",
        "## Legacy (Khavda-only, preserved)",
        "",
        "These routes existed before the multi-site store (DIYA v2 Phase C) "
        "and are thin wrappers that always resolve site_id=\"khavda\" internally "
        "(`/` and `/api/health` are global infra routes, not khavda-specific, "
        "but grouped here since they are not part of the multi-site feature set). "
        "Behavior is unchanged from before Phase C.",
        "",
    ]
    for method, path in LEGACY_ROUTES:
        lines.append(_render_route(method, path, schema, examples.get((method, path), [])))
        lines.append("")

    lines.append("## Site-scoped (multi-site)")
    lines.append("")
    lines.append(
        "These routes take a `site_id` (except the /api/sites collection routes) "
        "and work for any registered site, including khavda."
    )
    lines.append("")
    for method, path in SITE_SCOPED_ROUTES:
        lines.append(_render_route(method, path, schema, examples.get((method, path), [])))
        lines.append("")

    lines.append("## Convenience (Phase C.6, aggregates existing logic + geocoding)")
    lines.append("")
    lines.append(
        "These add no new solver/physics/KPI logic. overview/dispatch/evidence/export "
        "compose results already produced by the routes above (mainly _solve_live and "
        "the precomputed run files) into frontend-friendly shapes; geocode is the one "
        "genuinely new external call this phase adds (server-side OpenStreetMap Nominatim "
        "lookups -- see core/geocode.py for why this must never be called directly from "
        "browser JS)."
    )
    lines.append("")
    for method, path in CONVENIENCE_ROUTES:
        lines.append(_render_route(method, path, schema, examples.get((method, path), [])))
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    schema = api_main.app.openapi()
    OPENAPI_PATH.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")

    documented_paths = {(m, p) for m, p in LEGACY_ROUTES + SITE_SCOPED_ROUTES + CONVENIENCE_ROUTES}
    actual_paths = {
        (method.upper(), path) for path, methods in schema["paths"].items() for method in methods if method.upper() != "OPTIONS"
    }
    missing = actual_paths - documented_paths
    if missing:
        raise RuntimeError(
            f"scripts/generate_api_contract.py's LEGACY_ROUTES/SITE_SCOPED_ROUTES/CONVENIENCE_ROUTES "
            f"lists are out of date -- these routes exist in the real app but have no doc entry: {sorted(missing)}"
        )

    print("capturing real examples via TestClient (this includes one real ~60-65s /api/resolve call)...")
    examples = _capture_examples()

    markdown = _render_markdown(schema, examples)
    CONTRACT_PATH.write_text(markdown, encoding="utf-8")
    print(f"wrote {CONTRACT_PATH}")


if __name__ == "__main__":
    main()
