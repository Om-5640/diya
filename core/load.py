"""
DIYA core/load.py — synthetic, seeded, bottom-up load model for a Banni
(Khavda) hamlet cluster.

THIS IS A SYNTHETIC MODEL, NOT METERED SITE TELEMETRY. It follows the
methodology of open-source stochastic bottom-up demand generators such as
RAMP (Lombardi et al., "RAMP: A new stochastic model for generation and
consumption of electricity load profiles") — archetypes and daily-use
patterns below encode plausible assumptions about the site, not measured
consumption. Treat all output as a placeholder until real load metering is
available.

Archetype assumptions:
  - CRITICAL: primary health centre. Vaccine refrigeration + lighting run
    24/7 at 1.2 kW; OPD (outpatient) equipment adds 1.5 kW during clinic
    hours 09:00-17:00. Never zero — refrigeration must never lose its load
    signal.
  - ESSENTIAL: ~180 households with a typical rural-Gujarat daily rhythm —
    morning cooking/prep peak 06:00-09:00 (~14 kW), low midday while people
    are out working (~6 kW), a strong evening peak 18:00-23:00 (~26 kW) as
    households cook, light, and charge devices, and a night trough (~4 kW).
    Adds 2 kW of street lighting from 19:00-06:00. A lognormal multiplicative
    noise term represents household-to-household variability; a seasonal
    factor raises the evening peak in the hot months (Apr-Jun) for
    fan/cooler load.
  - DEFERRABLE: an RO water-treatment plant (8 kW) and a flour mill (6 kW),
    schedulable loads emitted here under a DEFAULT, unoptimised operating
    schedule (RO 06:00-11:00 daily, mill 10:00-13:00 on weekdays only) — a
    real dispatch policy may later choose to shift them.

UNIT CONVENTION: 1-hour timestep, kW/kWh; power in kW.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CRITICAL_BASELINE_KW = 1.2
CRITICAL_OPD_KW = 1.5
CRITICAL_OPD_HOURS = range(9, 17)

STREET_LIGHT_KW = 2.0
SUMMER_MONTHS = (4, 5, 6)
SUMMER_EVENING_BOOST = 1.15
SUMMER_EVENING_HOURS = range(18, 23)

HOUSEHOLD_NOISE_SIGMA = 0.06

# Piecewise-linear anchor profile (hour of day -> kW) hitting the target
# archetype levels: night trough ~4, morning peak ~14 (06:00-09:00), midday
# low ~6, evening peak ~26 (18:00-23:00), back to night trough at hour 24.
_ESSENTIAL_ANCHOR_HOURS = [0, 5, 6, 7, 8, 9, 12, 15, 17, 18, 19, 20, 21, 22, 23, 24]
_ESSENTIAL_ANCHOR_KW = [4, 4, 10, 14, 13, 8, 6, 6, 10, 20, 26, 26, 22, 14, 8, 4]

RO_KW = 8.0
RO_HOURS = range(6, 11)  # 06:00-11:00, daily
MILL_KW = 6.0
MILL_HOURS = range(10, 13)  # 10:00-13:00, weekdays only


def _essential_base_kw(hour_frac: np.ndarray) -> np.ndarray:
    return np.interp(hour_frac, _ESSENTIAL_ANCHOR_HOURS, _ESSENTIAL_ANCHOR_KW)


def generate_load(index: pd.DatetimeIndex, seed: int, load_multiplier: float = 1.0) -> pd.DataFrame:
    """Deterministic synthetic hourly load for `index`. See module docstring
    for the archetype assumptions this is built from. Byte-identical for a
    given (index, seed, load_multiplier).

    Returns a DataFrame with columns critical_kw, essential_kw, deferrable_kw.
    """
    rng = np.random.default_rng(seed)

    hour = index.hour.to_numpy()
    minute = index.minute.to_numpy()
    hour_frac = hour + minute / 60.0
    month = index.month.to_numpy()
    dayofweek = index.dayofweek.to_numpy()  # 0=Mon .. 6=Sun
    n = len(index)

    critical_kw = np.full(n, CRITICAL_BASELINE_KW) + np.where(
        np.isin(hour, list(CRITICAL_OPD_HOURS)), CRITICAL_OPD_KW, 0.0
    )

    essential_base = _essential_base_kw(hour_frac)
    is_summer_evening = np.isin(month, SUMMER_MONTHS) & np.isin(hour, list(SUMMER_EVENING_HOURS))
    essential_base = np.where(is_summer_evening, essential_base * SUMMER_EVENING_BOOST, essential_base)

    street_light = np.where((hour >= 19) | (hour < 6), STREET_LIGHT_KW, 0.0)
    noise = rng.lognormal(mean=0.0, sigma=HOUSEHOLD_NOISE_SIGMA, size=n)
    essential_kw = essential_base * noise + street_light

    ro_on = np.isin(hour, list(RO_HOURS))
    mill_on = np.isin(hour, list(MILL_HOURS)) & (dayofweek < 5)
    deferrable_kw = np.where(ro_on, RO_KW, 0.0) + np.where(mill_on, MILL_KW, 0.0)

    return pd.DataFrame(
        {
            "critical_kw": critical_kw * load_multiplier,
            "essential_kw": essential_kw * load_multiplier,
            "deferrable_kw": deferrable_kw * load_multiplier,
        },
        index=index,
    )
