import json
from pathlib import Path

from mac_studio_sniper.parser import parse_har, parse_html

FIXTURES = Path(__file__).parent / "fixtures"
GRID_HTML = (FIXTURES / "grid_sample.html").read_text(encoding="utf-8")


def test_parse_html_extracts_all_tiles_with_zero_errors():
    report = parse_html(GRID_HTML)
    assert report.errors == []
    assert {t.part_number for t in report.tiles} == {
        "G0MCHLL/A",
        "G0MDXLL/A",
        "G0MDYLL/A",
        "G13JKLL/A",
    }


def test_tile_fields():
    report = parse_html(GRID_HTML)
    by_part = {t.part_number: t for t in report.tiles}

    t = by_part["G0MDXLL/A"]
    assert t.chip == "M3 Ultra"
    assert t.ram_gb == 512  # from dimensionMemory filter
    assert t.price_usd == 7999.0
    assert t.url == "https://www.apple.com/shop/product/G0MDXLL/A/refurbished-mac-studio-m3-ultra"

    # Tile with no memory dimension and no RAM in title → ram unknown.
    assert by_part["G0MDYLL/A"].ram_gb is None
    assert by_part["G0MDYLL/A"].chip == "M3 Ultra"

    assert by_part["G13JKLL/A"].chip == "M2 Max"
    assert by_part["G13JKLL/A"].ram_gb == 32


def test_storage_gb_not_mistaken_for_ram():
    # G13JKLL/A has dimensionCapacity 512gb but dimensionMemory 32gb — the
    # memory key must win and capacity must never leak into ram_gb.
    report = parse_html(GRID_HTML)
    t = {t.part_number: t for t in report.tiles}["G13JKLL/A"]
    assert t.ram_gb == 32


def test_window_assignment_noise_is_not_an_error():
    report = parse_html(GRID_HTML)
    # The non-JSON window.notJson assignment and tile-less bootstrap blob
    # must not produce gate-0.1 errors.
    assert report.errors == []


def test_no_tiles_is_an_error():
    report = parse_html("<html><body>nothing here</body></html>")
    assert report.tiles == []
    assert report.errors


def test_parse_har_wraps_html_responses():
    har = {
        "log": {
            "entries": [
                {
                    "request": {"url": "https://www.apple.com/shop/refurbished/mac/mac-studio"},
                    "response": {"content": {"mimeType": "text/html", "text": GRID_HTML}},
                },
                {
                    "request": {"url": "https://tracking.example.com/pixel"},
                    "response": {"content": {"mimeType": "image/gif", "text": ""}},
                },
            ]
        }
    }
    report = parse_har(json.dumps(har))
    assert report.errors == []
    assert len(report.tiles) == 4


def test_parse_har_invalid_json_reports_error():
    report = parse_har("not json at all")
    assert report.tiles == []
    assert report.errors
