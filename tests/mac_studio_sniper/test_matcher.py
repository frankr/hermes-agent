from pathlib import Path

import pytest

from mac_studio_sniper.matcher import SniperConfig, match_tiles
from mac_studio_sniper.models import Tile

TARGETS_YAML = """
targets:
  - name: "M3 Ultra 512GB"
    priority: 1
    match: { chip: "M3 Ultra", ram_gb: 512 }
    max_price_usd: 8600
  - name: "M3 Ultra 256GB"
    priority: 2
    match: { chip: "M3 Ultra", ram_gb: 256 }
    max_price_usd: 6100
"""


@pytest.fixture
def config(tmp_path: Path) -> SniperConfig:
    p = tmp_path / "targets.yaml"
    p.write_text(TARGETS_YAML, encoding="utf-8")
    return SniperConfig.load(p)


def _tile(part="G1/A", title="Refurbished Mac Studio Apple M3 Ultra Chip", **kw) -> Tile:
    return Tile(part_number=part, title=title, **kw)


def test_exact_match_512(config):
    matches = match_tiles([_tile(ram_gb=512, price_usd=7999.0)], config)
    assert len(matches) == 1
    m = matches[0]
    assert m.target_name == "M3 Ultra 512GB"
    assert m.priority == 1
    assert not m.needs_verification


def test_wrong_chip_no_match(config):
    tile = _tile(title="Refurbished Mac Studio Apple M2 Max Chip", ram_gb=512, price_usd=3000.0)
    assert match_tiles([tile], config) == []


def test_wrong_ram_no_match(config):
    assert match_tiles([_tile(ram_gb=96, price_usd=4000.0)], config) == []


def test_over_cap_no_match(config):
    assert match_tiles([_tile(ram_gb=512, price_usd=8601.0)], config) == []


def test_unknown_ram_matches_with_verification_flag(config):
    # Chip matches, RAM not derivable from tile → alert but flag it.
    matches = match_tiles([_tile(ram_gb=None, price_usd=5999.0)], config)
    assert len(matches) == 1
    assert matches[0].needs_verification
    # Binds to the highest-priority target that could plausibly match.
    assert matches[0].target_name == "M3 Ultra 512GB"


def test_unknown_price_matches_with_verification_flag(config):
    matches = match_tiles([_tile(ram_gb=256, price_usd=None)], config)
    assert len(matches) == 1
    assert matches[0].target_name == "M3 Ultra 256GB"
    assert matches[0].needs_verification


def test_one_match_per_tile_highest_priority_wins(config):
    # 512GB tile could only match target 1; ensure no duplicate for target 2.
    matches = match_tiles([_tile(ram_gb=512, price_usd=7999.0)], config)
    assert len(matches) == 1


def test_empty_targets_refused(tmp_path):
    p = tmp_path / "targets.yaml"
    p.write_text("targets: []", encoding="utf-8")
    with pytest.raises(ValueError):
        SniperConfig.load(p)
