"""Supervisor: the long-running-agent hygiene layer around the hot path.

Deterministic checks (no LLM) that make the system survive weeks of
waiting. The LLM-driven self-heal step is isolated behind ``heal`` — it
produces a *bundle* of failure artifacts for a Claude agent and applies
only a verified flightplan patch (re-drilled before promotion). Everything
else here is plain code so any supervisor process can die and resume from
the state DB.

Commands (wired in cli.py, scheduled via cron/systemd timers):
  heartbeat      gate 4.1 — watcher liveness + poll freshness
  session-check  gate 2.3/4.2 — Apple ID session still authenticated
  drill          gates 2.1/2.2 — rehearse the strike path
  race-ready     compute + log the operational SLO snapshot
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .buyer import Buyer
from .flightplan import Flightplan
from .guardrails import MAX_DRILL_AGE_H
from .matcher import SniperConfig
from .notify import Notifier
from .state import StateDB

logger = logging.getLogger("mac_studio_sniper.supervisor")

POLL_FRESHNESS_MAX_S = 120.0       # race-ready requires a poll newer than this
HEARTBEAT_POLL_STALE_S = 900.0     # 15 min with no successful poll => page (gate 4.1)


@dataclass
class RaceReady:
    ready: bool
    reasons: list[str]

    def render(self) -> str:
        status = "✅ RACE-READY" if self.ready else "⛔ NOT race-ready"
        if self.ready:
            return status
        return status + ":\n- " + "\n- ".join(self.reasons)


def compute_race_ready(
    state: StateDB,
    flightplan: Flightplan,
    config: SniperConfig,
    now: Optional[float] = None,
    require_verified_flightplan: bool = True,
) -> RaceReady:
    now = now or time.time()
    reasons: list[str] = []

    last_poll = state.last_poll_ts()
    if last_poll is None:
        reasons.append("no successful poll on record")
    elif now - last_poll > POLL_FRESHNESS_MAX_S:
        reasons.append(f"last successful poll {int(now - last_poll)}s ago (> {int(POLL_FRESHNESS_MAX_S)}s)")

    session = state.last_check("session")
    if session is None:
        reasons.append("no session check on record")
    elif not session["ok"]:
        reasons.append(f"Apple ID session not authenticated ({session.get('notes')})")

    drill_age = state.last_passing_drill_age_h(now=now)
    if drill_age is None:
        reasons.append("no passing drill on record")
    elif drill_age > MAX_DRILL_AGE_H:
        reasons.append(f"last passing drill {drill_age:.1f}h old (> {MAX_DRILL_AGE_H:.0f}h)")

    if require_verified_flightplan and not flightplan.verified:
        reasons.append("flightplan not verified (awaiting G0 recon)")

    if config.stop_after_first_success and state.purchase_count() >= 1:
        reasons.append("already purchased — intentionally disarmed")

    return RaceReady(ready=not reasons, reasons=reasons)


class Supervisor:
    def __init__(
        self,
        config: SniperConfig,
        flightplan: Flightplan,
        state: StateDB,
        notifier: Notifier,
        state_dir: Path,
    ) -> None:
        self.config = config
        self.flightplan = flightplan
        self.state = state
        self.notifier = notifier
        self.state_dir = state_dir

    # -- heartbeat (gate 4.1) ------------------------------------------------

    def heartbeat(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        last_poll = self.state.last_poll_ts()
        ok = last_poll is not None and (now - last_poll) <= HEARTBEAT_POLL_STALE_S
        note = (
            "no successful poll ever"
            if last_poll is None
            else f"last poll {int(now - last_poll)}s ago"
        )
        self.state.record_check("heartbeat", ok, notes=note, now=now)
        if not ok:
            self.notifier.send_raw(
                f"💔 heartbeat FAILED: {note}. Watcher may be down — restart it."
            )
        return ok

    # -- session check (gate 2.3 / 4.2) --------------------------------------

    async def session_check(
        self, profile_dir: Path, browser_path: Optional[str] = None, now: Optional[float] = None
    ) -> bool:
        now = now or time.time()
        sc = self.flightplan.session_check
        if sc is None:
            self.state.record_check("session", False, notes="no session_check in flightplan", now=now)
            return False
        from playwright.async_api import async_playwright

        signed_in = False
        note = ""
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=True,
                    executable_path=browser_path or None,
                )
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(sc.url, wait_until="domcontentloaded", timeout=sc.timeout_ms)
                signed_in = await self._any_visible(page, sc.signed_in_selectors, sc.timeout_ms)
                signed_out = await self._any_visible(page, sc.signed_out_selectors, 2000)
                note = f"signed_in_marker={signed_in} signed_out_marker={signed_out}"
                signed_in = signed_in and not signed_out
                await context.close()
        except Exception as e:
            note = f"{type(e).__name__}: {e}"
            signed_in = False
        self.state.record_check("session", signed_in, notes=note, now=now)
        if not signed_in:
            self.notifier.send_raw(
                f"🔐 Apple ID session check FAILED ({note}). Re-run"
                " `recon-checkout` (or a login) to refresh the profile."
            )
        return signed_in

    @staticmethod
    async def _any_visible(page, selectors: list[str], timeout_ms: int) -> bool:
        for sel in selectors:
            try:
                await page.locator(sel).first.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    # -- drill (gates 2.1 / 2.2) ---------------------------------------------

    async def drill(
        self, buyer: Buyer, drill_product_url: Optional[str] = None, now: Optional[float] = None
    ) -> bool:
        url = drill_product_url or self.flightplan.drill_grid_url
        if not url:
            self.notifier.send_raw("drill skipped: no drill target URL configured")
            return False
        result = await buyer.drill(url)
        streak = self.state.consecutive_passing_drills()
        logger.info(
            "drill %s in %.0fms (streak=%d)",
            "PASS" if result.ok else f"FAIL@{result.failed_step}",
            result.duration_ms,
            streak,
        )
        return result.ok

    # -- SLO snapshot --------------------------------------------------------

    def race_ready_snapshot(self, now: Optional[float] = None) -> RaceReady:
        rr = compute_race_ready(self.state, self.flightplan, self.config, now=now)
        self.state.record_race_ready(rr.ready, rr.reasons, now=now)
        return rr
