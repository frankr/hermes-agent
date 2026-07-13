"""Extract refurb product tiles from Apple grid pages (HTML or HAR).

Apple embeds the refurb grid's tile data as JSON inside ``<script
type="application/json">`` blocks (historically under a key like
``REFURB_GRID_BOTTOM``) and sometimes as ``window.NAME = {…}`` assignments.
Key names have churned over the years, so rather than hard-coding one JSON
path this module scans every embedded JSON document for objects that *look
like* product tiles: a part number, a title, and (usually) a price.

Gate 0.1 of `.plans/mac-studio-refurb-sniper-goal.md` is measured with
exactly this code via ``python -m mac_studio_sniper parse --har grid.har``:
100% of Mac Studio tiles extracted, zero errors. When Apple next changes
the schema, the supervisor's self-heal loop patches here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .models import Tile, extract_ram_gb_from_text

_SCRIPT_JSON_RE = re.compile(
    r"<script[^>]+type=[\"']application/json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
# window.REFURB_GRID_BOTTOM = {...};  /  var PRODUCT_SELECTION_BOOTSTRAP = {...};
_WINDOW_ASSIGN_RE = re.compile(
    r"(?:window\.|var\s+|const\s+)([A-Z][A-Z0-9_]{3,})\s*=\s*(\{)", re.DOTALL
)
# Apple part numbers: e.g. "G0MCH0LL/A", "MQH63LL/A" (refurbs are G-prefixed).
_PART_NUMBER_RE = re.compile(r"^[A-Z0-9]{5,12}/[A-Z]$")
_PART_KEY_RE = re.compile(r"part.?number", re.IGNORECASE)
_PRICE_KEY_RE = re.compile(r"price|amount", re.IGNORECASE)
_TITLE_KEYS = ("title", "productTitle", "name", "productName", "seoTitle")
_URL_KEYS = ("productDetailsUrl", "url", "productUrl", "detailsUrl", "href")
_MEMORY_KEY_RE = re.compile(r"memory", re.IGNORECASE)


@dataclass
class ParseReport:
    """Extraction outcome — errors are load-bearing for gate 0.1."""

    tiles: list[Tile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    json_blobs_scanned: int = 0

    def merge(self, other: "ParseReport") -> None:
        self.tiles.extend(other.tiles)
        self.errors.extend(other.errors)
        self.json_blobs_scanned += other.json_blobs_scanned


# ---------------------------------------------------------------------------
# JSON discovery
# ---------------------------------------------------------------------------


def _iter_embedded_json(html: str) -> Iterator[tuple[str, str]]:
    """Yield (source_label, json_text) candidates embedded in an HTML page."""
    for i, m in enumerate(_SCRIPT_JSON_RE.finditer(html)):
        yield f"script/application-json[{i}]", m.group(1).strip()
    for m in _WINDOW_ASSIGN_RE.finditer(html):
        name, brace_start = m.group(1), m.start(2)
        blob = _balanced_json_object(html, brace_start)
        if blob:
            yield f"window.{name}", blob


def _balanced_json_object(text: str, start: int) -> Optional[str]:
    """Extract a brace-balanced object literal starting at ``start``.

    Good enough for Apple's server-rendered JSON (no naked ``}`` inside
    regex literals etc.); strings are skipped properly.
    """
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ---------------------------------------------------------------------------
# Tile recognition
# ---------------------------------------------------------------------------


def _walk_dicts(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_dicts(v)


def _find_part_number(d: dict[str, Any]) -> Optional[str]:
    for k, v in d.items():
        if _PART_KEY_RE.search(k) and isinstance(v, str) and _PART_NUMBER_RE.match(v.strip()):
            return v.strip()
    return None


def _find_title(d: dict[str, Any]) -> Optional[str]:
    for k in _TITLE_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _find_url(d: dict[str, Any]) -> Optional[str]:
    for k in _URL_KEYS:
        v = d.get(k)
        if isinstance(v, str) and "/shop/" in v:
            return v.strip()
    return None


def _coerce_price(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if 0 < v < 100_000 else None
    if isinstance(v, str):
        cleaned = v.replace("$", "").replace(",", "").strip()
        try:
            return _coerce_price(float(cleaned))
        except ValueError:
            return None
    return None


def _find_price(d: dict[str, Any]) -> Optional[float]:
    """First plausible USD amount under a price-ish key, searched recursively."""
    for sub in _walk_dicts(d):
        for k, v in sub.items():
            if not _PRICE_KEY_RE.search(k):
                continue
            if isinstance(v, (dict, list)):
                for inner in _walk_dicts(v):
                    for ik, iv in inner.items():
                        if _PRICE_KEY_RE.search(ik):
                            price = _coerce_price(iv)
                            if price is not None:
                                return price
            else:
                price = _coerce_price(v)
                if price is not None:
                    return price
    return None


def _find_ram_gb(d: dict[str, Any], title: str) -> Optional[int]:
    # Filter dimensions like {"dimensionMemory": "96gb"} beat title parsing.
    for sub in _walk_dicts(d):
        for k, v in sub.items():
            if _MEMORY_KEY_RE.search(k) and isinstance(v, str):
                m = re.search(r"(\d+)\s*[Gg][Bb]", v)
                if m:
                    return int(m.group(1))
    return extract_ram_gb_from_text(title)


def _tile_from_dict(d: dict[str, Any]) -> Optional[Tile]:
    part = _find_part_number(d)
    if not part:
        return None
    title = _find_title(d)
    if not title:
        return None
    return Tile(
        part_number=part,
        title=title,
        price_usd=_find_price(d),
        url=_find_url(d),
        ram_gb=_find_ram_gb(d, title),
        raw=d,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse_json_document(doc: Any, source: str = "json") -> ParseReport:
    report = ParseReport(json_blobs_scanned=1)
    seen: dict[str, Tile] = {}
    claimed: set[int] = set()
    for d in _walk_dicts(doc):
        if id(d) in claimed:
            continue
        tile = _tile_from_dict(d)
        if tile is None:
            continue
        # A tile dict claims its own subtree so nested price/filter dicts
        # aren't re-detected as separate (broken) tiles.
        for sub in _walk_dicts(d):
            claimed.add(id(sub))
        prev = seen.get(tile.part_number)
        if prev is None or (prev.price_usd is None and tile.price_usd is not None):
            seen[tile.part_number] = tile
    report.tiles = list(seen.values())
    if not report.tiles:
        report.errors.append(f"{source}: no product tiles recognized")
    return report


def parse_html(html: str, source: str = "html") -> ParseReport:
    report = ParseReport()
    for label, blob in _iter_embedded_json(html):
        try:
            doc = json.loads(blob)
        except json.JSONDecodeError:
            # Non-JSON window assignments (JS expressions) are expected noise,
            # not gate-0.1 errors.
            continue
        sub = parse_json_document(doc, source=f"{source}:{label}")
        report.json_blobs_scanned += sub.json_blobs_scanned
        # "no tiles in this blob" is only an error at page level, not per blob.
        report.tiles.extend(sub.tiles)
    # Dedup across blobs by part number, keeping the richest tile.
    by_part: dict[str, Tile] = {}
    for t in report.tiles:
        prev = by_part.get(t.part_number)
        if prev is None or (prev.price_usd is None and t.price_usd is not None):
            by_part[t.part_number] = t
    report.tiles = list(by_part.values())
    if not report.tiles:
        report.errors.append(
            f"{source}: no product tiles found in {report.json_blobs_scanned} embedded JSON blob(s)"
        )
    return report


def parse_har(har_text: str) -> ParseReport:
    """Extract tiles from every apple.com HTML/JSON response in a HAR file."""
    report = ParseReport()
    try:
        har = json.loads(har_text)
    except json.JSONDecodeError as e:
        report.errors.append(f"har: not valid JSON ({e})")
        return report
    entries = (har.get("log") or {}).get("entries") or []
    if not entries:
        report.errors.append("har: no log.entries")
        return report
    found_any = False
    for entry in entries:
        url = ((entry.get("request") or {}).get("url")) or ""
        if "apple.com" not in url:
            continue
        content = ((entry.get("response") or {}).get("content")) or {}
        text = content.get("text")
        if not text:
            continue
        mime = content.get("mimeType", "")
        if "html" in mime:
            sub = parse_html(text, source=url)
        elif "json" in mime:
            try:
                sub = parse_json_document(json.loads(text), source=url)
            except json.JSONDecodeError:
                continue
        else:
            continue
        if sub.tiles:
            found_any = True
            report.merge(ParseReport(tiles=sub.tiles, json_blobs_scanned=sub.json_blobs_scanned))
    if not found_any:
        report.errors.append("har: no apple.com response contained recognizable product tiles")
    # Dedup by part number across entries.
    by_part: dict[str, Tile] = {}
    for t in report.tiles:
        prev = by_part.get(t.part_number)
        if prev is None or (prev.price_usd is None and t.price_usd is not None):
            by_part[t.part_number] = t
    report.tiles = list(by_part.values())
    return report
