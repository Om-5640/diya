# DIYA — Freeze Notes

Feature freeze checklist and the headline numbers to have on hand during Q&A,
pulled directly from the real precomputed run JSON (`data/processed/runs/S2_*.json`)
— not retyped from memory.

## S2 (Monsoon Stretch) headline numbers

| Policy | diesel_l | cost_total_inr | co2_kg | renewable_frac | dg_starts |
|---|---:|---:|---:|---:|---:|
| diesel_only | 945.48 | 96,948.32 | 2,533.90 | 0.000 | 1 |
| rule_based | 568.25 | 56,933.76 | 1,522.90 | 0.464 | 7 |
| **mpc** | **403.08** | **41,754.76** | **1,080.25** | **0.567** | 13 |
| perfect_foresight | 287.46 | 32,632.20 | 770.40 | 0.632 | 8 |

Critical outage hours are **0.0 for every policy** on S2 — the story is cost/fuel/CO2
efficiency, not reliability, on this scenario.

**benefit_capture** (mpc's share of the cost gap between rule_based and
perfect_foresight) = **62.5%** — mpc closes just under two-thirds of the
theoretical gap between the naive baseline and the unattainable perfect-foresight
upper bound, using only real-time forecasts.

## Freeze checklist

- [x] `python scripts/smoke_test.py` passes on this machine (verified this session:
      all parquet/JSON/API checks PASS, `SMOKE TEST: PASS`, ~6s runtime)
- [ ] `python scripts/smoke_test.py` passes on a machine other than the one that
      built it — **not verified**: no second laptop was available in this session
      to actually run it on. Do this before the demo, don't skip it silently.
- [x] `python scripts/run_demo.py` starts both servers and Ctrl+C cleanly kills
      both (verified this session via a real CTRL_BREAK_EVENT signal, not just a
      force-kill: both URLs printed, `/api/health` confirmed 200, then on signal
      both the backend and the frontend's actual node process were gone from
      `netstat`/`Get-NetTCPConnection` afterward — see acceptance check output)
- [x] Operator Live works with the API server killed mid-session (static
      fallback) — verified with a screenshot in Phase 4: the status-warn notice
      appears, the view renders the static S2_mpc.json data, nothing crashes
- [x] Evidence's hero comparison (S2 rule_based vs mpc) loads with zero backend
      calls — verified in Phase 1B/4: browser network tab showed zero requests to
      :8000 with the API server both running and stopped
- [ ] A screen recording of the full demo sequence exists as a fallback for a
      live failure — **not done**. Record one before the event: Operator Live's
      3-second read, the village visual, a "Re-plan now" click, then Evidence's
      S2 rule_based-vs-mpc comparison with a hover and the Stress Test panel.
- [ ] Laptop is on mains power, HDMI/USB-C adapter tested, browser tabs for both
      views pre-opened before walking up — **physical/venue checklist, do this
      at the venue** — nothing to verify from here.

## Known, accepted limitations (don't get caught off guard by these in Q&A)

- `/api/resolve`'s full 168-hour re-solve takes **~60-65 seconds**, not a few
  seconds — this is CBC subprocess-spawn overhead across 168 sequential solves
  (see `api/main.py`'s comment above the endpoint), not a bug. The Evidence
  Stress Test panel's "Re-solving…" state says this explicitly so it doesn't
  look hung.
- `/api/solve_live`'s PV/wind conversion always uses Khavda's panel tilt/azimuth
  from `site_khavda.yaml` regardless of the requested lat/lon — a known
  simplification (see `api/main.py`'s `_live_pv_wind_forecast`).
- Diesel price, CO2 price, VOLL values, and the diesel fuel-curve coefficients
  in `config/site_khavda.yaml` are engineering assumptions, not measured —
  see the README's provenance table and the comment block at the top of that
  YAML file.
