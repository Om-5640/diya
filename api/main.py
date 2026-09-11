"""
DIYA api/main.py — FastAPI app.

PHASE 0: every route serves mock data via core.mock.make_mock_run. No real
weather fetching, forecasting, or optimization happens here yet — those are
later phases. response_model=RunResult on every run-returning route so
pydantic validates every response against the data contract in core/types.py.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.mock import POLICIES, SCENARIOS, make_mock_run
from core.types import RunResult

app = FastAPI(title="DIYA API", version="0.1.0-phase0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunListEntry(BaseModel):
    scenario_id: str
    policy: str
    name: str


class ResolveRequest(BaseModel):
    scenario_id: str
    weights: dict[str, float] = {}
    k_uncertainty: float = 1.0
    diesel_price_inr_per_l: float | None = None


class SolveLiveRequest(BaseModel):
    lat: float
    lon: float
    soc_pct: float


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "phase": 0}


@app.get("/api/runs", response_model=list[RunListEntry])
def list_runs() -> list[RunListEntry]:
    return [
        RunListEntry(scenario_id=scenario_id, policy=policy, name=meta["name"])
        for scenario_id, meta in SCENARIOS.items()
        for policy in POLICIES
    ]


@app.get("/api/runs/{scenario_id}/{policy}", response_model=RunResult)
def get_run(scenario_id: str, policy: str) -> RunResult:
    if scenario_id not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown scenario_id: {scenario_id}")
    if policy not in POLICIES:
        raise HTTPException(status_code=404, detail=f"unknown policy: {policy}")
    return make_mock_run(scenario_id, policy)


@app.post("/api/resolve", response_model=RunResult)
def resolve(req: ResolveRequest) -> RunResult:
    if req.scenario_id not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown scenario_id: {req.scenario_id}")
    run = make_mock_run(req.scenario_id, "mpc")
    run.notes = "Phase 0 mock resolve — request parameters are accepted but not yet applied."
    return run


@app.post("/api/solve_live", response_model=RunResult)
def solve_live(req: SolveLiveRequest) -> RunResult:
    run = make_mock_run("S1", "mpc", n_hours=72)
    run.notes = "Phase 0 mock live solve — request parameters are accepted but not yet applied."
    return run
