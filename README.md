# DIYA — Dispatch Intelligence for Yield & Autonomy

DIYA is a forecast-aware microgrid dispatch optimizer for an off-grid site at Khavda,
Kutch, Gujarat, India — a solar/wind/battery/diesel hybrid system serving a health
centre (critical load), household/business demand (essential load), and a deferrable
load (RO water plant / flour mill). The project compares dispatch policies
(`diesel_only`, `rule_based`, `mpc`, `perfect_foresight`) on fuel cost, CO2, and
unserved critical energy, and explains every dispatch decision with an operator-facing
reason code. **This is Phase 0**: repo scaffolding, pydantic data contracts, a mock
data generator, and a mock-serving API only. No weather fetching, forecasting, or
optimization logic exists yet — see `core/*.py` placeholders for what's coming.

## Unit convention

The timestep is exactly **1 hour**. All power is in **kW**, all energy is in **kWh**.
Because dt = 1h, kW and kWh are numerically identical per step — never introduce
another timestep. Every field name carries its unit suffix (`_kw`, `_kwh`, `_l`,
`_inr`, `_kg`, `_pct`).

## How to run

This project has no `make` dependency — everything runs through Python,
since that's the one tool every part of the stack already shares.

```bash
pip install -r requirements.txt
python scripts/build_scenarios.py      # only needed once, or after a config change
python scripts/precompute_runs.py      # only needed once, or after a config change
python scripts/smoke_test.py           # verify everything is intact
python scripts/run_demo.py             # starts both servers, prints URLs
```

`run_demo.py` starts the FastAPI backend and the Vite dev server together,
waits for both to report healthy, prints their URLs, and cleanly stops both
(no orphaned processes) on Ctrl+C.

## Config values: measured vs. assumed

See the comment block at the top of `config/site_khavda.yaml` for the full,
field-by-field breakdown. Summary:

| Category | Values | Notes |
|---|---|---|
| **Measured** | `lat`, `lon`, `elevation_m`, `timezone` | Site/geographic facts |
| **Public reference** | PV/wind/battery/diesel hardware specs, `diesel_price_inr_per_l` | Vendor datasheets or PPAC published pricing — not site-measured |
| **Engineering assumption** | `diesel.fuel_a_l_per_kw_h`, `diesel.fuel_b_l_per_kwh`, `diesel.start_cost_inr`, `economics.co2_price_inr_per_kg`, all `voll_*_inr_per_kwh` | Placeholder values chosen to be plausible; verify before any real financial or dispatch decision |

## Repo layout

- `core/types.py` — pydantic v2 data contracts (fully implemented)
- `core/mock.py` — deterministic fake-data generator (fully implemented)
- `core/{weather,pv,wind,load,forecast,milp,policies,simulator,kpi,reasons,runner}.py` — placeholders for later phases
- `api/main.py` — FastAPI app serving mock data only
- `config/site_khavda.yaml`, `config/scenarios.yaml` — site and scenario configuration
- `tests/test_contracts.py` — Phase 0 contract tests
- `web/` — owned by a separate workstream, not created here
