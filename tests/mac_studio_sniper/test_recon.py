"""Pure-logic recon tests (no browser). The Playwright capture/recorder
flow is exercised by integration runs — see RECON.md."""

from mac_studio_sniper.models import Tile
from mac_studio_sniper.recon import format_selector_report, suggest_price_caps


def _ultra(part, ram, price):
    return Tile(
        part_number=part,
        title="Refurbished Mac Studio Apple M3 Ultra Chip",
        ram_gb=ram,
        price_usd=price,
    )


def test_suggest_caps_from_observed_prices():
    tiles = [
        _ultra("G1/A", 512, 7999.0),
        _ultra("G2/A", 512, 8099.0),
        _ultra("G3/A", 256, 5599.0),
        _ultra("G4/A", 96, 4759.0),
    ]
    lines = "\n".join(suggest_price_caps(tiles))
    # cap = max * 1.02 rounded to nearest 50
    assert "512GB" in lines and "8250" in lines
    assert "256GB" in lines and "5700" in lines
    assert "other/unknown RAM" in lines


def test_suggest_caps_no_ultras():
    tiles = [
        Tile(part_number="G5/A", title="Refurbished Mac Studio Apple M2 Max Chip", price_usd=1699.0)
    ]
    lines = "\n".join(suggest_price_caps(tiles))
    assert "no M3 Ultra listings observed" in lines


def test_selector_report_prefers_data_autom_then_id_then_name():
    events = [
        {
            "kind": "click",
            "url": "https://www.apple.com/shop/bag",
            "element": {"tag": "button", "dataAutom": "proceed", "id": "x", "text": "Check Out"},
        },
        {
            "kind": "click",
            "url": "https://www.apple.com/shop/bag",
            "element": {"tag": "button", "id": "place-order", "text": "Place Order"},
        },
        {
            "kind": "click",
            "url": "https://www.apple.com/shop/checkout",
            "element": {"tag": "input", "name": "cvv", "text": ""},
        },
        {
            "kind": "click",
            "url": "https://www.apple.com/shop/checkout",
            "element": {"tag": "div", "text": "mystery element"},
        },
    ]
    report = format_selector_report(events)
    assert '[data-autom="proceed"]' in report
    assert "#place-order" in report
    assert 'input[name="cvv"]' in report
    assert "NEEDS MANUAL PICK" in report
    # Page grouping: both URLs present once as headers.
    assert report.count("== PAGE: https://www.apple.com/shop/bag") == 1
    assert report.count("== PAGE: https://www.apple.com/shop/checkout") == 1
