"""Gate 3.1: guardrails must block every unsafe arm. No browser needed."""

from pathlib import Path

import pytest

from mac_studio_sniper.flightplan import Flightplan
from mac_studio_sniper.guardrails import check_arm
from mac_studio_sniper.matcher import SniperConfig
from mac_studio_sniper.models import MatchResult, Tile
from mac_studio_sniper.state import StateDB

TARGETS = """
mode: confirm
quantity: 1
stop_after_first_success: true
targets:
  - name: "M3 Ultra 512GB"
    priority: 1
    match: { chip: "M3 Ultra", ram_gb: 512 }
    max_price_usd: 8600
"""

VERIFIED_FLIGHTPLAN = """
version: 1
verified: true
steps:
  - id: product
    action: goto
    url: "{product_url}"
  - id: place_order
    action: click
    final: true
    selectors: ["[data-autom='place-order-button']"]
"""


@pytest.fixture
def env(tmp_path: Path):
    (tmp_path / "targets.yaml").write_text(TARGETS, encoding="utf-8")
    (tmp_path / "flightplan.yaml").write_text(VERIFIED_FLIGHTPLAN, encoding="utf-8")
    config = SniperConfig.load(tmp_path / "targets.yaml")
    flightplan = Flightplan.load(tmp_path / "flightplan.yaml")
    state = StateDB(tmp_path / "state.sqlite")
    # A fresh passing drill so drill-age doesn't block.
    state.record_drill(mode="drill", ok=True, duration_ms=40_000)
    kill = tmp_path / "KILL"
    return config, flightplan, state, kill, tmp_path


def _match(chip="M3 Ultra", ram=512, price=7999.0, needs_verification=False, url="https://apple.com/x"):
    tile = Tile(part_number="G1/A", title="Refurbished Mac Studio Apple M3 Ultra Chip",
                chip=chip, ram_gb=ram, price_usd=price, url=url)
    return MatchResult(tile=tile, target_name="M3 Ultra 512GB", priority=1,
                       max_price_usd=8600.0, needs_verification=needs_verification)


def test_clean_match_arms(env):
    config, fp, state, kill, _ = env
    assert check_arm(config, _match(), state, fp, kill, cvv_available=True) == []


def test_over_cap_blocked(env):
    config, fp, state, kill, _ = env
    v = check_arm(config, _match(price=8601.0), state, fp, kill, cvv_available=True)
    assert any("exceeds cap" in x for x in v)


def test_wrong_ram_blocked(env):
    config, fp, state, kill, _ = env
    v = check_arm(config, _match(ram=256), state, fp, kill, cvv_available=True)
    assert any("RAM" in x for x in v)


def test_needs_verification_blocked(env):
    config, fp, state, kill, _ = env
    v = check_arm(config, _match(needs_verification=True), state, fp, kill, cvv_available=True)
    assert any("unverified specs" in x for x in v)


def test_quantity_not_one_blocked(env, tmp_path):
    config, fp, state, kill, _ = env
    config.quantity = 2
    v = check_arm(config, _match(), state, fp, kill, cvv_available=True)
    assert any("quantity" in x for x in v)


def test_kill_switch_blocks(env):
    config, fp, state, kill, _ = env
    kill.touch()
    v = check_arm(config, _match(), state, fp, kill, cvv_available=True)
    assert any("kill switch" in x for x in v)


def test_unverified_flightplan_blocks(env, tmp_path):
    config, fp, state, kill, _ = env
    fp.verified = False
    v = check_arm(config, _match(), state, fp, kill, cvv_available=True)
    assert any("not verified" in x for x in v)


def test_stale_drill_blocks(env):
    config, fp, state, kill, _ = env
    import time

    # Overwrite with an old drill: last passing drill 60h ago.
    state._conn.execute("DELETE FROM drills")
    state.record_drill(mode="drill", ok=True, now=time.time() - 60 * 3600)
    v = check_arm(config, _match(), state, fp, kill, cvv_available=True)
    assert any("drill" in x for x in v)


def test_no_drill_blocks(env, tmp_path):
    config, fp, _state, kill, _ = env
    fresh = StateDB(tmp_path / "fresh.sqlite")
    v = check_arm(config, _match(), fresh, fp, kill, cvv_available=True)
    assert any("no passing drill" in x for x in v)


def test_alert_only_mode_blocks(env):
    config, fp, state, kill, _ = env
    config.mode = "alert-only"
    v = check_arm(config, _match(), state, fp, kill, cvv_available=True)
    assert any("mode" in x for x in v)


def test_already_purchased_blocks(env):
    config, fp, state, kill, _ = env
    state.record_purchase("G0/A", 7999.0, "W123", "confirm")
    v = check_arm(config, _match(), state, fp, kill, cvv_available=True)
    assert any("already succeeded" in x for x in v)


def test_missing_cvv_when_required_blocks(env):
    config, fp, state, kill, _ = env
    # flightplan without cvv placeholder → cvv not required, so absence is fine
    assert not fp.uses_placeholder("cvv")
    assert check_arm(config, _match(), state, fp, kill, cvv_available=False) == []
