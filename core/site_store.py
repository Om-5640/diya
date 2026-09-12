"""
DIYA core/site_store.py — multi-site registry (Phase C of DIYA v2).

Manages sites/registry.json, a JSON list of SiteRecord entries pointing at
each site's SiteConfig YAML and scenarios/runs directories. Khavda's existing
legacy layout (config/site_khavda.yaml, data/processed/, data/processed/runs/)
is registered as a seed entry pointing AT those existing paths -- nothing
under config/, data/, or existing precomputed runs is moved, renamed, or
otherwise touched by this module. New sites created via SiteStore.create get
their own fresh directory under sites/{site_id}/.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from core.types import (
    BatterySpec,
    DieselSpec,
    EconomicsSpec,
    NewSiteRequest,
    PVSpec,
    SiteConfig,
    SiteRecord,
    WindSpec,
    load_site_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SITES_DIR = REPO_ROOT / "sites"
REGISTRY_PATH = SITES_DIR / "registry.json"

KHAVDA_SITE_ID = "khavda"

# Khavda's real seed record: paths point at its existing legacy layout
# (config/site_khavda.yaml, data/processed/ for scenario parquet data,
# data/processed/runs for precomputed runs) -- not a sites/khavda/
# directory. scenarios_dir holds scenario DATA (S1.parquet etc.), mirroring
# what scenarios_dir means for a new site's own sites/{id}/scenarios/ --
# human-readable scenario names/cfgs still come from config/scenarios.yaml,
# a khavda-specific concept the API layer reads directly, unrelated to this
# registry. This module never writes to any of khavda's paths except via
# update()'s economics-only path, and never deletes them.
_KHAVDA_SEED_RECORD = {
    "site_id": KHAVDA_SITE_ID,
    "display_name": "Khavda Off-Grid Microgrid",
    "lat": 23.8443,
    "lon": 69.7317,
    "config_path": "config/site_khavda.yaml",
    "scenarios_dir": "data/processed",
    "runs_dir": "data/processed/runs",
    "created_at": "2026-01-01T00:00:00+00:00",
    "is_seed": True,
}

# Combustion-property constants reused as-is for every new site's diesel
# genset (fuel curve shape / CO2 factor are properties of diesel combustion,
# not of a particular site) -- only rated_kw is site-specific, taken from
# NewSiteRequest.diesel_rated_kw.
_DIESEL_MIN_LOAD_FRAC = 0.30
_DIESEL_FUEL_A_L_PER_KW_H = 0.08
_DIESEL_FUEL_B_L_PER_KWH = 0.25
_DIESEL_START_COST_INR = 150.0
_DIESEL_CO2_KG_PER_L = 2.68
_CO2_PRICE_INR_PER_KG = 2.0

# VOLL ratios relative to the requested outage_cost_inr_per_kwh: critical =
# the full cost, essential = 15%, deferrable = 3% -- the same relative
# shape as Khavda's hand-tuned 500/60/12 (60/500=0.12, 12/500=0.024,
# rounded to clean 0.15/0.03 for a generic default rather than copying
# Khavda's exact figures onto an unrelated site).
_VOLL_ESSENTIAL_RATIO = 0.15
_VOLL_DEFERRABLE_RATIO = 0.03


class SiteNotFoundError(Exception):
    """Raised by get/load_config/update/delete for an unknown site_id."""


class SeedSiteProtectedError(Exception):
    """Raised when an operation would delete, or change a disallowed field
    of, a seed site (currently only khavda)."""


def _default_site_config(site_id: str, req: NewSiteRequest) -> SiteConfig:
    """Derive a full SiteConfig from the minimal NewSiteRequest. Every
    default below is a physically-reasoned placeholder (tilt = latitude,
    array facing the equator, battery sized at a 3-hour C-rate), not a
    trained/fitted value -- see the DIYA v2 Phase C discussion for why no
    ML is used here.
    """
    tilt_deg = round(abs(req.lat), 1)
    azimuth_deg = 180.0 if req.lat >= 0 else 0.0  # face the equator

    pv = PVSpec(
        capacity_kwp=req.pv_capacity_kwp,
        tilt_deg=tilt_deg,
        azimuth_deg=azimuth_deg,
        temp_coeff_per_c=-0.004,
        derate=0.90,
    )

    # Wind is not part of the minimal new-site input; default it "omitted"
    # by zeroing rated_kw -- WindSpec is still a required SiteConfig field,
    # so the other parameters are carried over as inert placeholders that
    # never contribute power while rated_kw stays 0.
    wind = WindSpec(
        rated_kw=0.0,
        hub_height_m=10.0,
        cut_in_ms=3.0,
        rated_ms=11.0,
        cut_out_ms=20.0,
        shear_exp=0.14,
    )

    battery_c_rate_kw = req.battery_capacity_kwh / 3.0
    battery = BatterySpec(
        capacity_kwh=req.battery_capacity_kwh,
        soc_min_pct=20.0,
        soc_max_pct=95.0,
        p_charge_max_kw=battery_c_rate_kw,
        p_discharge_max_kw=battery_c_rate_kw,
        eta_charge=0.95,
        eta_discharge=0.95,
        soc_init_pct=60.0,
    )

    diesel = DieselSpec(
        rated_kw=req.diesel_rated_kw,
        min_load_frac=_DIESEL_MIN_LOAD_FRAC,
        fuel_a_l_per_kw_h=_DIESEL_FUEL_A_L_PER_KW_H,
        fuel_b_l_per_kwh=_DIESEL_FUEL_B_L_PER_KWH,
        start_cost_inr=_DIESEL_START_COST_INR,
        co2_kg_per_l=_DIESEL_CO2_KG_PER_L,
    )

    economics = EconomicsSpec(
        diesel_price_inr_per_l=req.diesel_price_inr_per_l,
        diesel_price_source="user-provided at site creation (default, not measured)",
        co2_price_inr_per_kg=_CO2_PRICE_INR_PER_KG,
        voll_critical_inr_per_kwh=req.outage_cost_inr_per_kwh * 1.0,
        voll_essential_inr_per_kwh=req.outage_cost_inr_per_kwh * _VOLL_ESSENTIAL_RATIO,
        voll_deferrable_inr_per_kwh=req.outage_cost_inr_per_kwh * _VOLL_DEFERRABLE_RATIO,
    )

    return SiteConfig(
        site_id=site_id,
        name=req.display_name,
        lat=req.lat,
        lon=req.lon,
        elevation_m=req.elevation_m,
        timezone=req.timezone,
        pv=pv,
        wind=wind,
        battery=battery,
        diesel=diesel,
        economics=economics,
        horizon_hours=24,
        reserve_hours=3,
        k_uncertainty=1.0,
    )


class SiteStore:
    """Reads/writes a registry JSON file (sites/registry.json by default).
    Every method re-reads/re-writes the file rather than caching in memory,
    so multiple SiteStore instances (e.g. a test using a tmp_path registry
    alongside the real one) never see each other's stale state.
    """

    def __init__(
        self,
        registry_path: Path | str = REGISTRY_PATH,
        site_dir_base: Path | str = SITES_DIR,
        khavda_config_path: Path | str | None = None,
    ):
        """`site_dir_base` is where new sites' own directories are created
        (defaults to the real repo's sites/ dir). Tests pass a tmp_path for
        both `registry_path` and `site_dir_base` so nothing under the real
        sites/ or config/ trees is ever touched by a test run.

        `khavda_config_path` overrides where the auto-seeded khavda record
        points its config_path -- production leaves this None (real repo's
        config/site_khavda.yaml, matching config/scenarios.yaml and
        data/processed/runs exactly as before). A test that calls update()
        against khavda (a real, supported operation -- economics changes
        are allowed on seed sites) MUST pass a path to its own throwaway
        copy of the YAML here, since update() genuinely writes to whatever
        this resolves to and must never write to the real, live-demo file.
        """
        self.registry_path = Path(registry_path)
        self.site_dir_base = Path(site_dir_base)
        self._khavda_config_path = str(khavda_config_path) if khavda_config_path is not None else None
        self._ensure_seeded()

    def _ensure_seeded(self) -> None:
        if self.registry_path.exists():
            return
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        seed = dict(_KHAVDA_SEED_RECORD)
        if self._khavda_config_path is not None:
            seed["config_path"] = self._khavda_config_path
        self.registry_path.write_text(json.dumps([seed], indent=2), encoding="utf-8")

    def _read_all(self) -> list[dict]:
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _write_all(self, records: list[dict]) -> None:
        self.registry_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    @staticmethod
    def resolve_path(path_str: str) -> Path:
        """A record's config_path/scenarios_dir/runs_dir is stored either
        absolute (created sites, anchored at whatever site_dir_base was in
        effect when they were created) or relative to REPO_ROOT (the khavda
        seed's paths, which always mean the real repo's existing files)."""
        p = Path(path_str)
        return p if p.is_absolute() else REPO_ROOT / p

    def list(self) -> list[SiteRecord]:
        return [SiteRecord.model_validate(r) for r in self._read_all()]

    def get(self, site_id: str) -> SiteRecord:
        for r in self._read_all():
            if r["site_id"] == site_id:
                return SiteRecord.model_validate(r)
        raise SiteNotFoundError(f"no site registered with site_id={site_id!r}")

    def load_config(self, site_id: str) -> SiteConfig:
        record = self.get(site_id)
        return load_site_config(self.resolve_path(record.config_path))

    def runs_dir_path(self, site_id: str) -> Path:
        return self.resolve_path(self.get(site_id).runs_dir)

    def scenarios_dir_path(self, site_id: str) -> Path:
        return self.resolve_path(self.get(site_id).scenarios_dir)

    def create(self, req: NewSiteRequest) -> SiteRecord:
        site_id = f"site_{uuid.uuid4().hex[:8]}"
        site_dir = self.site_dir_base / site_id
        (site_dir / "scenarios").mkdir(parents=True, exist_ok=True)
        (site_dir / "runs").mkdir(parents=True, exist_ok=True)

        config = _default_site_config(site_id, req)
        config_path = site_dir / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )

        record = SiteRecord(
            site_id=site_id,
            display_name=req.display_name,
            lat=req.lat,
            lon=req.lon,
            config_path=str(config_path.resolve()),
            scenarios_dir=str((site_dir / "scenarios").resolve()),
            runs_dir=str((site_dir / "runs").resolve()),
            created_at=datetime.now(timezone.utc).isoformat(),
            is_seed=False,
        )
        records = self._read_all()
        records.append(json.loads(record.model_dump_json()))
        self._write_all(records)
        return record

    def update(self, site_id: str, updates: dict) -> SiteConfig:
        """Merge `updates` (top-level SiteConfig field overrides, e.g.
        {"economics": {...}}) into the site's config.yaml. For the khavda
        seed site, only economics/horizon_hours/reserve_hours/k_uncertainty
        may change -- any attempt to touch hardware specs (pv/wind/battery/
        diesel/lat/lon/etc.) raises SeedSiteProtectedError, since Khavda's
        hardware is the real, already-demoed configuration and must not be
        silently rewritten by an API call.
        """
        record = self.get(site_id)
        config = self.load_config(site_id)

        if record.is_seed:
            allowed = {"economics", "horizon_hours", "reserve_hours", "k_uncertainty"}
            disallowed = set(updates.keys()) - allowed
            if disallowed:
                raise SeedSiteProtectedError(
                    f"site {site_id!r} is a seed site; only {sorted(allowed)} may be "
                    f"updated, got disallowed field(s): {sorted(disallowed)}"
                )

        # model_copy(update=...) does NOT re-validate, so a raw dict handed
        # in for a nested field (e.g. {"economics": {...}}) would otherwise
        # sit there as a plain dict instead of an EconomicsSpec. Merge into
        # the full dump and re-validate the whole SiteConfig instead, so
        # every nested spec is a real, validated model afterward.
        merged = config.model_dump(mode="json")
        merged.update(updates)
        new_config = SiteConfig.model_validate(merged)
        config_path = self.resolve_path(record.config_path)
        config_path.write_text(
            yaml.safe_dump(new_config.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        return new_config

    def delete(self, site_id: str) -> None:
        record = self.get(site_id)
        if record.is_seed:
            raise SeedSiteProtectedError(f"site {site_id!r} is a seed site and cannot be deleted")

        records = [r for r in self._read_all() if r["site_id"] != site_id]
        self._write_all(records)

        site_dir = self.site_dir_base / site_id
        if site_dir.exists() and site_dir.resolve().parent == self.site_dir_base.resolve():
            shutil.rmtree(site_dir, ignore_errors=True)
