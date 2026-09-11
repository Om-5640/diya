"""
DIYA tests/test_contracts.py — Phase 0 contract tests.

Validates: mock RunResults conform to the pydantic contracts, all 12 mock
files exist and parse, every reason_code is a known key, the site config
loads and hashes stably, and SOC stays within configured bounds. No
optimization/forecast/UI logic exists yet to test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.mock import POLICIES, SCENARIOS, make_mock_run, write_mock_files
from core.types import REASON_CODES, RunResult, config_hash, load_site_config

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_CONFIG_PATH = REPO_ROOT / "config" / "site_khavda.yaml"
MOCK_DIR = REPO_ROOT / "data" / "processed" / "mock"


@pytest.fixture(scope="module")
def mock_files() -> list[Path]:
    return write_mock_files(MOCK_DIR)


def test_mock_run_validates_against_contract() -> None:
    run = make_mock_run("S2", "mpc", seed=0)
    assert isinstance(run, RunResult)
    assert len(run.steps) == 168


def test_all_twelve_mock_files_exist_and_parse(mock_files: list[Path]) -> None:
    expected = {f"{s}_{p}.json" for s in SCENARIOS for p in POLICIES}
    actual = {p.name for p in mock_files}
    assert actual == expected
    assert len(expected) == 12

    for path in mock_files:
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        run = RunResult.model_validate(data)
        assert len(run.steps) == 168


def test_reason_codes_are_known(mock_files: list[Path]) -> None:
    for path in mock_files:
        run = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
        for step in run.steps:
            assert step.reason_code in REASON_CODES, f"unknown reason_code {step.reason_code} in {path.name}"


def test_load_site_config_parses_and_hashes_stably() -> None:
    config = load_site_config(SITE_CONFIG_PATH)
    assert config.site_id == "khavda_01"
    assert config.pv.capacity_kwp == 60

    h1 = config_hash(config)
    h2 = config_hash(load_site_config(SITE_CONFIG_PATH))
    assert h1 == h2
    assert len(h1) == 8


def test_soc_within_bounds(mock_files: list[Path]) -> None:
    config = load_site_config(SITE_CONFIG_PATH)
    soc_min = config.battery.soc_min_pct
    soc_max = config.battery.soc_max_pct

    for path in mock_files:
        run = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
        for step in run.steps:
            assert soc_min - 1e-6 <= step.soc_pct <= soc_max + 1e-6, (
                f"soc_pct {step.soc_pct} out of bounds in {path.name} at {step.t}"
            )
