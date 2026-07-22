"""Phase-0 availability watch: broad config matches any M3 Ultra, sightings
logged, report reads them. No purchase machinery involved."""

import asyncio
import time
from pathlib import Path

from mac_studio_sniper.cli import main
from mac_studio_sniper.matcher import SniperConfig, match_tiles
from mac_studio_sniper.parser import parse_html
from mac_studio_sniper.state import StateDB
from mac_studio_sniper.watcher import Watcher

REPO = Path(__file__).resolve().parents[2] / "mac_studio_sniper"
FIXTURES = Path(__file__).parent / "fixtures"


def test_availability_config_matches_all_m3_ultra():
    config = SniperConfig.load(REPO / "targets.availability.yaml")
    tiles = parse_html((FIXTURES / "grid_sample.html").read_text(encoding="utf-8")).tiles
    matches = match_tiles(tiles, config)
    # The fixture has three M3 Ultra tiles (96GB, 512GB, and one unknown-RAM).
    ultra = [t for t in tiles if t.chip == "M3 Ultra"]
    assert len(matches) == len(ultra)
    # None should be blocked/needs-verification purely for price (no caps here).
    by_name = {m.tile.part_number: m for m in matches}
    # 512GB tile binds to the specific 512 target, not the catch-all.
    assert by_name["G0MDXLL/A"].target_name == "M3 Ultra 512GB"
    # 96GB tile has no matching specific target → catch-all.
    assert by_name["G0MCHLL/A"].target_name == "M3 Ultra (any config)"


class _NullNotifier:
    def __init__(self):
        self.alerts = []

    def channels(self):
        return ["console"]

    def send_match_alert(self, m):
        self.alerts.append(m)
        return ["console"]


def test_sightings_logged_and_report(tmp_path, capsys):
    config = SniperConfig.load(REPO / "targets.availability.yaml")
    state = StateDB(tmp_path / "state.sqlite")
    notifier = _NullNotifier()
    watcher = Watcher(config=config, state=state, notifier=notifier,
                      state_dir=tmp_path, on_match=None)
    tiles = parse_html((FIXTURES / "grid_sample.html").read_text(encoding="utf-8")).tiles
    # Simulate a poll landing these tiles.
    state.record_poll("u", ok=True, status=200, latency_ms=100, tiles=len(tiles),
                      now=time.time() - 3600)
    asyncio.run(watcher.handle_tiles(tiles))

    # Every M3 Ultra sighting is queryable regardless of match/price.
    ultras = state.sightings_matching("M3 Ultra")
    assert len(ultras) == 3
    assert any(s["ram_gb"] == 512 for s in ultras)

    # report command runs and names the configs.
    rc = main(["--state-dir", str(tmp_path), "report"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "M3 Ultra sightings: 3" in out
    assert "512GB seen: 1" in out


def test_report_empty(tmp_path, capsys):
    StateDB(tmp_path / "state.sqlite")
    rc = main(["--state-dir", str(tmp_path), "report"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No M3 Ultra sightings recorded yet" in out
