import asyncio
import json
from pathlib import Path

from mac_studio_sniper.matcher import SniperConfig
from mac_studio_sniper.models import MatchResult
from mac_studio_sniper.state import StateDB
from mac_studio_sniper.watcher import Watcher

TARGETS_YAML = """
realert_window_h: 24
targets:
  - name: "M3 Ultra 512GB"
    priority: 1
    match: { chip: "M3 Ultra", ram_gb: 512 }
    max_price_usd: 8600
"""


class FakeNotifier:
    def __init__(self):
        self.alerts: list[MatchResult] = []

    def channels(self):
        return ["console"]

    def send_match_alert(self, match):
        self.alerts.append(match)
        return ["console"]


def _watcher(tmp_path: Path, on_match=None) -> tuple[Watcher, FakeNotifier]:
    targets = tmp_path / "targets.yaml"
    targets.write_text(TARGETS_YAML, encoding="utf-8")
    notifier = FakeNotifier()
    watcher = Watcher(
        config=SniperConfig.load(targets),
        state=StateDB(tmp_path / "state.sqlite"),
        notifier=notifier,  # type: ignore[arg-type]
        state_dir=tmp_path,
        on_match=on_match,
    )
    return watcher, notifier


def _inject(tmp_path: Path, part="GTEST1/A", ram=512, price=7999.0):
    tile = {
        "part_number": part,
        "title": "Refurbished Mac Studio Apple M3 Ultra Chip",
        "ram_gb": ram,
        "price_usd": price,
    }
    (tmp_path / "inject").mkdir(exist_ok=True)
    (tmp_path / "inject" / f"{part.replace('/', '_')}.json").write_text(
        json.dumps([tile]), encoding="utf-8"
    )


def test_injection_fires_alert_and_hook(tmp_path: Path):
    hook_calls = []

    async def on_match(match):
        hook_calls.append(match)

    watcher, notifier = _watcher(tmp_path, on_match=on_match)
    _inject(tmp_path)

    async def run():
        tiles = watcher.scan_injections()
        assert len(tiles) == 1
        fired = await watcher.handle_tiles(tiles)
        assert len(fired) == 1

    asyncio.run(run())
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0].target_name == "M3 Ultra 512GB"
    assert len(hook_calls) == 1
    # Injection file consumed.
    assert list((tmp_path / "inject").glob("*.json")) == []


def test_duplicate_sighting_does_not_realert(tmp_path: Path):
    watcher, notifier = _watcher(tmp_path)

    async def run():
        _inject(tmp_path)
        await watcher.handle_tiles(watcher.scan_injections())
        _inject(tmp_path)  # same part number again
        fired = await watcher.handle_tiles(watcher.scan_injections())
        assert fired == []

    asyncio.run(run())
    assert len(notifier.alerts) == 1


def test_non_matching_tile_recorded_but_not_alerted(tmp_path: Path):
    watcher, notifier = _watcher(tmp_path)

    async def run():
        _inject(tmp_path, part="GM2MAX/A", ram=32)
        # Wrong RAM → sighting recorded, no alert.
        tiles = watcher.scan_injections()
        tiles[0].title = "Refurbished Mac Studio Apple M2 Max Chip"
        tiles[0].chip = "M2 Max"
        await watcher.handle_tiles(tiles)

    asyncio.run(run())
    assert notifier.alerts == []
    assert watcher.state.summary()["sightings"] == 1


def test_kill_switch_halts_run(tmp_path: Path):
    watcher, _ = _watcher(tmp_path)
    (tmp_path / "KILL").touch()

    async def run():
        await asyncio.wait_for(watcher.run(), timeout=2)

    asyncio.run(run())  # returns promptly instead of looping forever
