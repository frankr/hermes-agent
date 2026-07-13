import time
from pathlib import Path

from mac_studio_sniper.flightplan import Flightplan
from mac_studio_sniper.matcher import SniperConfig
from mac_studio_sniper.state import StateDB
from mac_studio_sniper.supervisor import compute_race_ready

VERIFIED_FP = """
version: 1
verified: true
steps:
  - id: product
    action: goto
    url: "{product_url}"
  - id: place_order
    action: click
    final: true
    selectors: ["#x"]
"""

TARGETS = """
mode: confirm
targets:
  - name: "M3 Ultra 512GB"
    priority: 1
    match: { chip: "M3 Ultra", ram_gb: 512 }
    max_price_usd: 8600
"""


def _env(tmp_path: Path, verified: bool = True):
    fp_text = VERIFIED_FP if verified else VERIFIED_FP.replace("verified: true", "verified: false")
    (tmp_path / "flightplan.yaml").write_text(fp_text, encoding="utf-8")
    (tmp_path / "targets.yaml").write_text(TARGETS, encoding="utf-8")
    fp = Flightplan.load(tmp_path / "flightplan.yaml")
    config = SniperConfig.load(tmp_path / "targets.yaml")
    state = StateDB(tmp_path / "state.sqlite")
    return fp, config, state


def _make_ready(state: StateDB, now: float):
    state.record_poll("u", ok=True, status=200, latency_ms=100, now=now - 10)
    state.record_check("session", True, now=now - 60)
    state.record_drill(mode="drill", ok=True, now=now - 3600)


def test_race_ready_all_green(tmp_path):
    now = 1_000_000.0
    fp, config, state = _env(tmp_path)
    _make_ready(state, now)
    rr = compute_race_ready(state, fp, config, now=now)
    assert rr.ready, rr.reasons


def test_race_ready_stale_poll(tmp_path):
    now = 1_000_000.0
    fp, config, state = _env(tmp_path)
    state.record_poll("u", ok=True, status=200, latency_ms=100, now=now - 300)
    state.record_check("session", True, now=now - 60)
    state.record_drill(mode="drill", ok=True, now=now - 3600)
    rr = compute_race_ready(state, fp, config, now=now)
    assert not rr.ready
    assert any("poll" in r for r in rr.reasons)


def test_race_ready_session_invalid(tmp_path):
    now = 1_000_000.0
    fp, config, state = _env(tmp_path)
    state.record_poll("u", ok=True, status=200, latency_ms=100, now=now - 10)
    state.record_check("session", False, notes="signed out", now=now - 60)
    state.record_drill(mode="drill", ok=True, now=now - 3600)
    rr = compute_race_ready(state, fp, config, now=now)
    assert not rr.ready
    assert any("session" in r for r in rr.reasons)


def test_race_ready_stale_drill(tmp_path):
    now = 1_000_000.0
    fp, config, state = _env(tmp_path)
    state.record_poll("u", ok=True, status=200, latency_ms=100, now=now - 10)
    state.record_check("session", True, now=now - 60)
    state.record_drill(mode="drill", ok=True, now=now - 60 * 3600)
    rr = compute_race_ready(state, fp, config, now=now)
    assert not rr.ready
    assert any("drill" in r for r in rr.reasons)


def test_race_ready_unverified_flightplan(tmp_path):
    now = 1_000_000.0
    fp, config, state = _env(tmp_path, verified=False)
    _make_ready(state, now)
    rr = compute_race_ready(state, fp, config, now=now)
    assert not rr.ready
    assert any("verified" in r for r in rr.reasons)


def test_race_ready_rate_slo(tmp_path):
    now = 1_000_000.0
    _, _, state = _env(tmp_path)
    for i in range(95):
        state.record_race_ready(True, [], now=now - i * 60)
    for i in range(5):
        state.record_race_ready(False, ["x"], now=now - (95 + i) * 60)
    rate = state.race_ready_rate(window_h=168, now=now)
    assert abs(rate - 0.95) < 1e-9
