from pathlib import Path

import pytest

from mac_studio_sniper.flightplan import Flightplan

GOOD = """
version: 2
verified: true
drill_grid_url: "http://x/accessories"
session_check:
  url: "http://x/bag"
  signed_in_selectors: ["[data-autom='sign-out']"]
  signed_out_selectors: ["[data-autom='sign-in']"]
steps:
  - id: product
    action: goto
    url: "{product_url}"
  - id: cvv
    action: fill
    selectors: ["input[name='cvv']"]
    value: "{cvv}"
  - id: place_order
    action: click
    final: true
    selectors: ["[data-autom='place-order-button']"]
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "flightplan.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_good(tmp_path):
    fp = Flightplan.load(_write(tmp_path, GOOD))
    assert fp.version == 2 and fp.verified
    assert fp.final_step.id == "place_order"
    assert fp.uses_placeholder("cvv")
    assert fp.uses_placeholder("product_url")
    assert fp.session_check.url == "http://x/bag"


def test_repo_flightplan_is_valid_and_unverified():
    # The shipped placeholder must parse and must NOT claim verified.
    fp = Flightplan.load(Path(__file__).resolve().parents[2]
                         / "mac_studio_sniper" / "flightplan.yaml")
    assert fp.verified is False
    assert fp.final_step is not None
    assert fp.validate() == []


def test_final_must_be_last(tmp_path):
    bad = """
version: 1
verified: false
steps:
  - id: a
    action: click
    final: true
    selectors: ["#a"]
  - id: b
    action: click
    selectors: ["#b"]
"""
    with pytest.raises(ValueError, match="final step must be the last"):
        Flightplan.load(_write(tmp_path, bad))


def test_multiple_finals_rejected(tmp_path):
    bad = """
version: 1
verified: false
steps:
  - id: a
    action: click
    final: true
    selectors: ["#a"]
  - id: b
    action: click
    final: true
    selectors: ["#b"]
"""
    with pytest.raises(ValueError, match="multiple steps marked final"):
        Flightplan.load(_write(tmp_path, bad))


def test_goto_requires_url(tmp_path):
    bad = """
version: 1
verified: false
steps:
  - id: a
    action: goto
"""
    with pytest.raises(ValueError, match="goto requires url"):
        Flightplan.load(_write(tmp_path, bad))


def test_click_requires_selectors(tmp_path):
    bad = """
version: 1
verified: false
steps:
  - id: a
    action: click
"""
    with pytest.raises(ValueError, match="requires selectors"):
        Flightplan.load(_write(tmp_path, bad))
