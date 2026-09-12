"""
DIYA tests/test_site_store.py — Phase C: SiteStore registry tests.

Every test uses a tmp_path registry (and tmp_path site_dir_base) -- never
the real sites/registry.json, and never the real config/site_khavda.yaml.
Any test that calls SiteStore.update() against khavda passes a throwaway
copy of the real YAML via khavda_config_path, since update() genuinely
writes to whatever that resolves to.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.site_store import KHAVDA_SITE_ID, SeedSiteProtectedError, SiteNotFoundError, SiteStore
from core.types import NewSiteRequest, load_site_config

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_KHAVDA_CONFIG = REPO_ROOT / "config" / "site_khavda.yaml"


def _store(tmp_path: Path, khavda_config_path: Path | None = None) -> SiteStore:
    return SiteStore(
        registry_path=tmp_path / "registry.json",
        site_dir_base=tmp_path / "sites",
        khavda_config_path=khavda_config_path,
    )


def _new_site_request(**overrides) -> NewSiteRequest:
    defaults = dict(
        display_name="Test Site",
        lat=-33.9,
        lon=18.4,
        pv_capacity_kwp=10.0,
        battery_capacity_kwh=30.0,
    )
    defaults.update(overrides)
    return NewSiteRequest(**defaults)


# ---------------------------------------------------------------------------
# Auto-seed correctness
# ---------------------------------------------------------------------------


def test_auto_seed_creates_registry_with_exactly_one_khavda_entry(tmp_path):
    registry_path = tmp_path / "registry.json"
    assert not registry_path.exists()

    store = _store(tmp_path)

    assert registry_path.exists()
    records = store.list()
    assert len(records) == 1
    assert records[0].site_id == KHAVDA_SITE_ID
    assert records[0].is_seed is True
    assert records[0].display_name == "Khavda Off-Grid Microgrid"


def test_auto_seed_is_idempotent_across_instances(tmp_path):
    store1 = _store(tmp_path)
    store2 = _store(tmp_path)  # same registry_path -- must not reseed/duplicate

    assert len(store1.list()) == 1
    assert len(store2.list()) == 1


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_khavda_matches_load_site_config_directly(tmp_path):
    store = _store(tmp_path)
    via_store = store.load_config(KHAVDA_SITE_ID)
    direct = load_site_config(REAL_KHAVDA_CONFIG)
    assert via_store.model_dump() == direct.model_dump()


def test_get_and_load_config_unknown_site_raise(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(SiteNotFoundError):
        store.get("does_not_exist")
    with pytest.raises(SiteNotFoundError):
        store.load_config("does_not_exist")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_produces_valid_files_and_registry_entry(tmp_path):
    store = _store(tmp_path)
    req = _new_site_request(lat=-33.9, lon=18.4, pv_capacity_kwp=10.0, battery_capacity_kwh=30.0)

    record = store.create(req)

    assert record.site_id != KHAVDA_SITE_ID
    assert record.is_seed is False
    assert Path(record.config_path).exists()
    assert Path(record.scenarios_dir).is_dir()
    assert Path(record.runs_dir).is_dir()

    listed = store.list()
    assert any(r.site_id == record.site_id for r in listed)

    config = store.load_config(record.site_id)
    assert config.site_id == record.site_id
    assert config.name == "Test Site"
    assert config.pv.capacity_kwp == 10.0
    assert config.battery.capacity_kwh == 30.0
    # tilt = abs(lat) rounded to 1 decimal; southern hemisphere -> azimuth 0
    assert config.pv.tilt_deg == pytest.approx(33.9)
    assert config.pv.azimuth_deg == 0.0
    # wind omitted -> rated_kw 0
    assert config.wind.rated_kw == 0.0
    # battery C-rate default: capacity_kwh / 3
    assert config.battery.p_charge_max_kw == pytest.approx(10.0)
    assert config.battery.p_discharge_max_kw == pytest.approx(10.0)
    # diesel reuses khavda's combustion-property constants
    assert config.diesel.min_load_frac == pytest.approx(0.30)
    assert config.diesel.fuel_a_l_per_kw_h == pytest.approx(0.08)
    assert config.diesel.fuel_b_l_per_kwh == pytest.approx(0.25)
    assert config.diesel.co2_kg_per_l == pytest.approx(2.68)
    # VOLL ratios 1.0 / 0.15 / 0.03 of outage_cost_inr_per_kwh (default 500)
    assert config.economics.voll_critical_inr_per_kwh == pytest.approx(500.0)
    assert config.economics.voll_essential_inr_per_kwh == pytest.approx(75.0)
    assert config.economics.voll_deferrable_inr_per_kwh == pytest.approx(15.0)
    assert config.horizon_hours == 24
    assert config.reserve_hours == 3
    assert config.k_uncertainty == pytest.approx(1.0)


def test_create_northern_hemisphere_azimuth_180(tmp_path):
    store = _store(tmp_path)
    req = _new_site_request(lat=23.8443, lon=69.7317)
    record = store.create(req)
    config = store.load_config(record.site_id)
    assert config.pv.azimuth_deg == 180.0
    assert config.pv.tilt_deg == pytest.approx(23.8)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_new_site_succeeds(tmp_path):
    store = _store(tmp_path)
    record = store.create(_new_site_request())
    site_dir = Path(record.config_path).parent

    store.delete(record.site_id)

    assert not any(r.site_id == record.site_id for r in store.list())
    assert not site_dir.exists()
    with pytest.raises(SiteNotFoundError):
        store.get(record.site_id)


def test_delete_khavda_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(SeedSiteProtectedError):
        store.delete(KHAVDA_SITE_ID)
    # still present afterward
    assert store.get(KHAVDA_SITE_ID) is not None


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_economics_ok_on_khavda(tmp_path):
    copy_path = tmp_path / "site_khavda_copy.yaml"
    shutil.copy(REAL_KHAVDA_CONFIG, copy_path)
    store = _store(tmp_path, khavda_config_path=copy_path)

    original = store.load_config(KHAVDA_SITE_ID)
    new_econ = original.economics.model_dump()
    new_econ["diesel_price_inr_per_l"] = 999.0

    updated = store.update(KHAVDA_SITE_ID, {"economics": new_econ})

    assert updated.economics.diesel_price_inr_per_l == pytest.approx(999.0)
    reread = store.load_config(KHAVDA_SITE_ID)
    assert reread.economics.diesel_price_inr_per_l == pytest.approx(999.0)

    # the real, live-demo file must be completely untouched
    real = load_site_config(REAL_KHAVDA_CONFIG)
    assert real.economics.diesel_price_inr_per_l == pytest.approx(92.5)


def test_update_hardware_spec_rejected_on_khavda(tmp_path):
    copy_path = tmp_path / "site_khavda_copy.yaml"
    shutil.copy(REAL_KHAVDA_CONFIG, copy_path)
    store = _store(tmp_path, khavda_config_path=copy_path)

    with pytest.raises(SeedSiteProtectedError):
        store.update(KHAVDA_SITE_ID, {"battery": {"capacity_kwh": 1.0}})
    with pytest.raises(SeedSiteProtectedError):
        store.update(KHAVDA_SITE_ID, {"lat": 0.0})

    # untouched after the rejected attempts
    reread = store.load_config(KHAVDA_SITE_ID)
    assert reread.battery.capacity_kwh == pytest.approx(120.0)


def test_update_new_site_allows_any_field(tmp_path):
    store = _store(tmp_path)
    record = store.create(_new_site_request())

    updated = store.update(record.site_id, {"battery": {**store.load_config(record.site_id).battery.model_dump(), "capacity_kwh": 999.0}})
    assert updated.battery.capacity_kwh == pytest.approx(999.0)
