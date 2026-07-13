"""Core datatypes shared across the sniper modules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

APPLE_BASE_URL = "https://www.apple.com"

# "Apple M3 Ultra Chip", "M2 Max", "Apple M4 chip" …
_CHIP_RE = re.compile(r"\bM(\d+)\s*(Ultra|Max|Pro)?\b", re.IGNORECASE)
# "96GB", "512 GB", "96gb of memory" …
_GB_RE = re.compile(r"(\d+)\s*[Gg][Bb]")


@dataclass
class Tile:
    """One product tile from the refurb grid (or a synthetic injection)."""

    part_number: str
    title: str
    price_usd: Optional[float] = None
    url: Optional[str] = None
    chip: Optional[str] = None       # e.g. "M3 Ultra"
    ram_gb: Optional[int] = None     # None = not derivable from the tile
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.chip is None:
            self.chip = extract_chip(self.title)
        if self.ram_gb is None:
            self.ram_gb = extract_ram_gb_from_text(self.title)
        if self.url and self.url.startswith("/"):
            self.url = APPLE_BASE_URL + self.url

    @property
    def product_url(self) -> str:
        return self.url or f"{APPLE_BASE_URL}/shop/refurbished/mac/mac-studio"


@dataclass
class MatchResult:
    """A tile that satisfied (or plausibly satisfies) a configured target."""

    tile: Tile
    target_name: str
    priority: int
    max_price_usd: Optional[float]
    # True when the tile lacked data (usually RAM) to fully confirm the
    # target spec. Phase 1 alerts on these anyway — a human can verify in
    # seconds via the deep link — but the Phase 2 buyer must never arm on
    # an unverified match.
    needs_verification: bool = False

    def headline(self) -> str:
        price = f"${self.tile.price_usd:,.2f}" if self.tile.price_usd is not None else "price unknown"
        flag = " [UNVERIFIED SPECS]" if self.needs_verification else ""
        return f"[P{self.priority}] {self.target_name}{flag}: {self.tile.title} — {price}"


def extract_chip(text: str) -> Optional[str]:
    m = _CHIP_RE.search(text)
    if not m:
        return None
    gen, variant = m.group(1), m.group(2)
    return f"M{gen} {variant.capitalize()}" if variant else f"M{gen}"


def extract_ram_gb_from_text(text: str) -> Optional[int]:
    """Best-effort RAM from free text.

    Grid tile titles usually carry chip/CPU/GPU but not RAM; product-detail
    titles and filter dimensions usually do. Ignore values that are clearly
    storage (>= 1024 or names like "1TB" never match this regex anyway) —
    Mac Studio RAM options are 32–512 GB, storage GB options start at 512
    too, so a lone "512GB" is ambiguous. We only trust values adjacent to
    the word "memory"; otherwise return None and let the matcher flag the
    tile as needing verification.
    """
    lowered = text.lower()
    for m in _GB_RE.finditer(text):
        start, end = m.span()
        window = lowered[max(0, start - 24) : min(len(lowered), end + 24)]
        if "memory" in window or "unified" in window or "ram" in window:
            return int(m.group(1))
    return None
