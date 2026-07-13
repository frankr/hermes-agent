"""Code-enforced arming guardrails — gate 3.1's test surface.

``check_arm`` returns the list of violations standing between a match and
a live purchase. Empty list = armed. No agent, prompt, config comment, or
webpage content can override these: the buyer refuses to execute the
final step unless this function returns [] at strike time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .flightplan import Flightplan
from .matcher import SniperConfig, TargetSpec
from .models import MatchResult
from .state import StateDB

MAX_DRILL_AGE_H = 48.0
ARMABLE_MODES = ("confirm", "full-auto")


def _find_target(config: SniperConfig, name: str) -> Optional[TargetSpec]:
    for t in config.targets:
        if t.name == name:
            return t
    return None


def check_arm(
    config: SniperConfig,
    match: MatchResult,
    state: StateDB,
    flightplan: Flightplan,
    kill_switch: Path,
    cvv_available: bool,
    max_drill_age_h: float = MAX_DRILL_AGE_H,
) -> list[str]:
    v: list[str] = []

    # -- mode & lifecycle ----------------------------------------------------
    if config.mode not in ARMABLE_MODES:
        v.append(f"mode is {config.mode!r} — arming requires confirm or full-auto")
    if kill_switch.exists():
        v.append(f"kill switch present at {kill_switch}")
    if config.stop_after_first_success and state.purchase_count() >= 1:
        v.append("a purchase already succeeded — system is disarmed")
    if config.quantity != 1:
        v.append(f"quantity must be exactly 1 (configured: {config.quantity})")

    # -- flightplan readiness -------------------------------------------------
    if not flightplan.verified:
        v.append("flightplan is not verified (G0 recon selectors not confirmed)")
    if flightplan.final_step is None:
        v.append("flightplan has no final (place-order) step")
    drill_age = state.last_passing_drill_age_h()
    if drill_age is None:
        v.append("no passing drill on record")
    elif drill_age > max_drill_age_h:
        v.append(f"last passing drill is {drill_age:.1f}h old (max {max_drill_age_h:.0f}h)")
    if flightplan.uses_placeholder("cvv") and not cvv_available:
        v.append("flightplan requires CVV but no CVV secret is configured")

    # -- the match itself -------------------------------------------------------
    target = _find_target(config, match.target_name)
    if target is None:
        v.append(f"match target {match.target_name!r} not in targets.yaml allowlist")
    else:
        if match.needs_verification:
            v.append("match has unverified specs — alert-only, never buy")
        tile = match.tile
        if not tile.chip or tile.chip.lower() != target.chip.lower():
            v.append(f"tile chip {tile.chip!r} != target chip {target.chip!r}")
        if target.ram_gb is not None and tile.ram_gb != target.ram_gb:
            v.append(f"tile RAM {tile.ram_gb!r} != target RAM {target.ram_gb}")
        if target.max_price_usd is not None:
            if tile.price_usd is None:
                v.append("tile price unknown — cannot enforce price cap")
            elif tile.price_usd > target.max_price_usd:
                v.append(
                    f"price ${tile.price_usd:,.2f} exceeds cap ${target.max_price_usd:,.2f}"
                )
        if not tile.url:
            v.append("tile has no product URL")

    return v
