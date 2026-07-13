from pathlib import Path

from mac_studio_sniper.models import MatchResult, Tile
from mac_studio_sniper.state import StateDB


def _tile(part="G1/A") -> Tile:
    return Tile(part_number=part, title="Refurbished Mac Studio Apple M3 Ultra Chip", price_usd=5000.0)


def _match(part="G1/A") -> MatchResult:
    return MatchResult(tile=_tile(part), target_name="t", priority=1, max_price_usd=6000.0)


def test_sighting_new_then_seen(tmp_path: Path):
    db = StateDB(tmp_path / "s.sqlite")
    assert db.record_sighting(_tile()) is True
    assert db.record_sighting(_tile()) is False


def test_alert_dedup_window(tmp_path: Path):
    db = StateDB(tmp_path / "s.sqlite")
    now = 1_000_000.0
    assert not db.recently_alerted("G1/A", window_h=24, now=now)
    db.record_alert(_match(), channels=["console"], now=now)
    assert db.recently_alerted("G1/A", window_h=24, now=now + 3600)
    # Outside the window it may re-alert.
    assert not db.recently_alerted("G1/A", window_h=24, now=now + 25 * 3600)


def test_poll_stats(tmp_path: Path):
    db = StateDB(tmp_path / "s.sqlite")
    now = 1_000_000.0
    for i in range(99):
        db.record_poll("u", ok=True, status=200, latency_ms=100, now=now + i)
    db.record_poll("u", ok=False, status=403, latency_ms=50, blocked=True, now=now + 99)
    stats = db.poll_stats(window_h=72, now=now + 100)
    assert stats["polls"] == 100
    assert stats["success_rate"] == 0.99
    assert stats["block_events"] == 1
