"""The detection hot loop.

Deterministic, no LLM anywhere. Polls each configured endpoint on a
jittered interval (tightened inside configured hot windows), parses tiles,
dedups against the state DB, matches against targets, and fires the
notifier plus the ``on_match`` hook (the Phase 2 buyer) immediately.

Synthetic injection (gates 1.3 / 3.2): any ``*.json`` file dropped into
``<state_dir>/inject/`` is consumed on the next tick (sub-second, the
inject scan runs at 250 ms) and treated exactly like tiles that came from
a live poll — same dedup, same matching, same alert path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .matcher import SniperConfig, match_tiles
from .models import MatchResult, Tile
from .notify import Notifier
from .parser import parse_html
from .state import StateDB
from .transport import fetch, jittered_interval

logger = logging.getLogger("mac_studio_sniper.watcher")

DEFAULT_ENDPOINTS = [
    "https://www.apple.com/shop/refurbished/mac/mac-studio",
]

OnMatch = Callable[[MatchResult], Awaitable[None]]


@dataclass
class WatcherSettings:
    endpoints: list[str] = field(default_factory=lambda: list(DEFAULT_ENDPOINTS))
    base_interval_s: float = 30.0
    hot_interval_s: float = 10.0
    # Local hours [start, end) treated as hot windows (drop-prone periods).
    hot_hours_local: list[list[int]] = field(default_factory=lambda: [[4, 8]])
    inject_scan_s: float = 0.25

    @classmethod
    def from_config(cls, config: SniperConfig) -> "WatcherSettings":
        w = config.watch
        return cls(
            endpoints=list(w.get("endpoints") or DEFAULT_ENDPOINTS),
            base_interval_s=float(w.get("base_interval_s", 30)),
            hot_interval_s=float(w.get("hot_interval_s", 10)),
            hot_hours_local=[list(h) for h in w.get("hot_hours_local", [[4, 8]])],
            inject_scan_s=float(w.get("inject_scan_s", 0.25)),
        )

    def in_hot_window(self, now: Optional[datetime] = None) -> bool:
        hour = (now or datetime.now()).hour
        return any(start <= hour < end for start, end in self.hot_hours_local)

    def current_interval(self) -> float:
        base = self.hot_interval_s if self.in_hot_window() else self.base_interval_s
        return jittered_interval(base)


class Watcher:
    def __init__(
        self,
        config: SniperConfig,
        state: StateDB,
        notifier: Notifier,
        state_dir: Path,
        on_match: Optional[OnMatch] = None,
    ) -> None:
        self.config = config
        self.settings = WatcherSettings.from_config(config)
        self.state = state
        self.notifier = notifier
        self.on_match = on_match
        self.inject_dir = state_dir / "inject"
        self.inject_dir.mkdir(parents=True, exist_ok=True)
        self.kill_switch = state_dir / "KILL"
        self._etags: dict[str, str] = {}

    # -- one poll cycle ------------------------------------------------------

    async def poll_endpoint(self, url: str) -> list[Tile]:
        result = await asyncio.to_thread(fetch, url, self._etags.get(url))
        tiles: list[Tile] = []
        if result.not_modified:
            self.state.record_poll(url, ok=True, status=304, latency_ms=result.latency_ms)
            return tiles
        if result.ok:
            report = parse_html(result.text, source=url)
            tiles = report.tiles
            if result.etag:
                self._etags[url] = result.etag
            self.state.record_poll(
                url, ok=True, status=result.status, latency_ms=result.latency_ms, tiles=len(tiles)
            )
            if report.errors:
                logger.warning("parse issues for %s: %s", url, report.errors)
        else:
            self.state.record_poll(
                url,
                ok=False,
                status=result.status,
                latency_ms=result.latency_ms,
                blocked=result.blocked,
                error=result.error,
            )
            if result.blocked:
                logger.error("BLOCK event on %s (%s)", url, result.error)
        return tiles

    def scan_injections(self) -> list[Tile]:
        tiles: list[Tile] = []
        for f in sorted(self.inject_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for d in data if isinstance(data, list) else [data]:
                    tiles.append(
                        Tile(
                            part_number=d["part_number"],
                            title=d["title"],
                            price_usd=d.get("price_usd"),
                            url=d.get("url"),
                            ram_gb=d.get("ram_gb"),
                        )
                    )
                logger.info("consumed injection %s (%d tiles)", f.name, len(tiles))
            except Exception:
                logger.exception("bad injection file %s", f)
            finally:
                f.unlink(missing_ok=True)
        return tiles

    async def handle_tiles(self, tiles: list[Tile]) -> list[MatchResult]:
        """Dedup, match, alert, fire buyer hook. Returns fresh alerted matches."""
        fresh = [t for t in tiles if self.state.record_sighting(t)]
        if not fresh:
            return []
        matches = match_tiles(fresh, self.config)
        fired: list[MatchResult] = []
        for match in matches:
            if self.state.recently_alerted(
                match.tile.part_number, self.config.realert_window_h
            ):
                continue
            channels = self.notifier.send_match_alert(match)
            self.state.record_alert(match, channels)
            fired.append(match)
            if self.on_match is not None:
                try:
                    await self.on_match(match)
                except Exception:
                    logger.exception("on_match hook failed for %s", match.tile.part_number)
        for t in fresh:
            logger.info("new sighting: %s — %s ($%s)", t.part_number, t.title, t.price_usd)
        return fired

    # -- main loop -------------------------------------------------------------

    async def run(self) -> None:
        logger.info(
            "watcher up: %d endpoint(s), base=%ss hot=%ss, channels=%s",
            len(self.settings.endpoints),
            self.settings.base_interval_s,
            self.settings.hot_interval_s,
            self.notifier.channels(),
        )
        next_poll = 0.0
        while True:
            if self.kill_switch.exists():
                logger.critical("kill switch present (%s) — halting", self.kill_switch)
                return
            injected = self.scan_injections()
            if injected:
                await self.handle_tiles(injected)
            now = time.monotonic()
            if now >= next_poll:
                next_poll = now + self.settings.current_interval()
                for url in self.settings.endpoints:
                    tiles = await self.poll_endpoint(url)
                    if tiles:
                        await self.handle_tiles(tiles)
            await asyncio.sleep(self.settings.inject_scan_s)
