"""Match parsed tiles against ``targets.yaml``.

The targets file is the ONLY thing that authorizes spending money — the
matcher is deliberately conservative: unknown price or unknown RAM can
still produce an *alert* (needs_verification=True) but such a match must
never arm the buyer (enforced again in the Phase 2 buyer itself).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .models import MatchResult, Tile


@dataclass
class TargetSpec:
    name: str
    priority: int
    chip: str
    ram_gb: Optional[int]
    max_price_usd: Optional[float]


@dataclass
class SniperConfig:
    targets: list[TargetSpec]
    mode: str = "alert-only"          # alert-only | confirm | full-auto
    stop_after_first_success: bool = True
    quantity: int = 1
    confirm_timeout_s: int = 120
    realert_window_h: float = 24.0    # suppress duplicate alerts per part number
    watch: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "SniperConfig":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        targets = []
        for t in data.get("targets", []):
            match = t.get("match", {})
            targets.append(
                TargetSpec(
                    name=t["name"],
                    priority=int(t.get("priority", 99)),
                    chip=str(match.get("chip", "")).strip(),
                    ram_gb=int(match["ram_gb"]) if match.get("ram_gb") is not None else None,
                    max_price_usd=(
                        float(t["max_price_usd"]) if t.get("max_price_usd") is not None else None
                    ),
                )
            )
        if not targets:
            raise ValueError(f"{path}: no targets defined — refusing to run")
        return cls(
            targets=sorted(targets, key=lambda t: t.priority),
            mode=data.get("mode", "alert-only"),
            stop_after_first_success=bool(data.get("stop_after_first_success", True)),
            quantity=int(data.get("quantity", 1)),
            confirm_timeout_s=int(data.get("confirm_timeout_s", 120)),
            realert_window_h=float(data.get("realert_window_h", 24)),
            watch=data.get("watch", {}) or {},
        )


def match_tile(tile: Tile, target: TargetSpec) -> Optional[MatchResult]:
    if not tile.chip or tile.chip.lower() != target.chip.lower():
        return None
    needs_verification = False
    if target.ram_gb is not None:
        if tile.ram_gb is None:
            # Chip matches but the tile doesn't state RAM — alert, flag it.
            needs_verification = True
        elif tile.ram_gb != target.ram_gb:
            return None
    if target.max_price_usd is not None:
        if tile.price_usd is None:
            needs_verification = True
        elif tile.price_usd > target.max_price_usd:
            return None
    return MatchResult(
        tile=tile,
        target_name=target.name,
        priority=target.priority,
        max_price_usd=target.max_price_usd,
        needs_verification=needs_verification,
    )


def match_tiles(tiles: list[Tile], config: SniperConfig) -> list[MatchResult]:
    """Best (lowest-priority-number) match per tile, sorted by priority."""
    results: list[MatchResult] = []
    for tile in tiles:
        for target in config.targets:  # already priority-sorted
            m = match_tile(tile, target)
            if m:
                results.append(m)
                break
    return sorted(results, key=lambda m: (m.priority, m.needs_verification))
