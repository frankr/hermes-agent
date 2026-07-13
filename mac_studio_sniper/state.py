"""SQLite state: seen tiles, alerts, poll metrics.

This DB is the system's durable memory — the watcher, the future buyer,
and the supervisor all read/write here, so any process can die and resume.
Gate 1.1 (poll success rate) and gate 1.4 (alert dedup) are computed from
these tables by ``cli.py status``.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

from .models import MatchResult, Tile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    part_number TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    chip        TEXT,
    ram_gb      INTEGER,
    price_usd   REAL,
    url         TEXT,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    times_seen  INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    part_number TEXT NOT NULL,
    target_name TEXT NOT NULL,
    price_usd   REAL,
    needs_verification INTEGER NOT NULL,
    channels    TEXT NOT NULL,
    ts          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS polls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint    TEXT NOT NULL,
    ts          REAL NOT NULL,
    ok          INTEGER NOT NULL,
    status      INTEGER,
    latency_ms  REAL,
    blocked     INTEGER NOT NULL DEFAULT 0,
    tiles       INTEGER,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_polls_ts ON polls (ts);
CREATE INDEX IF NOT EXISTS idx_alerts_part_ts ON alerts (part_number, ts);
"""


class StateDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- sightings ---------------------------------------------------------

    def record_sighting(self, tile: Tile, now: Optional[float] = None) -> bool:
        """Upsert a tile sighting. Returns True when this part is NEW."""
        now = now or time.time()
        cur = self._conn.execute(
            "SELECT 1 FROM sightings WHERE part_number = ?", (tile.part_number,)
        )
        is_new = cur.fetchone() is None
        if is_new:
            self._conn.execute(
                "INSERT INTO sightings (part_number, title, chip, ram_gb, price_usd, url,"
                " first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                (
                    tile.part_number,
                    tile.title,
                    tile.chip,
                    tile.ram_gb,
                    tile.price_usd,
                    tile.url,
                    now,
                    now,
                ),
            )
        else:
            self._conn.execute(
                "UPDATE sightings SET last_seen = ?, times_seen = times_seen + 1,"
                " price_usd = COALESCE(?, price_usd) WHERE part_number = ?",
                (now, tile.price_usd, tile.part_number),
            )
        self._conn.commit()
        return is_new

    # -- alerts --------------------------------------------------------------

    def recently_alerted(
        self, part_number: str, window_h: float, now: Optional[float] = None
    ) -> bool:
        now = now or time.time()
        cur = self._conn.execute(
            "SELECT 1 FROM alerts WHERE part_number = ? AND ts > ? LIMIT 1",
            (part_number, now - window_h * 3600),
        )
        return cur.fetchone() is not None

    def record_alert(
        self, match: MatchResult, channels: list[str], now: Optional[float] = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO alerts (part_number, target_name, price_usd, needs_verification,"
            " channels, ts) VALUES (?,?,?,?,?,?)",
            (
                match.tile.part_number,
                match.target_name,
                match.tile.price_usd,
                int(match.needs_verification),
                ",".join(channels),
                now or time.time(),
            ),
        )
        self._conn.commit()

    # -- poll metrics ---------------------------------------------------------

    def record_poll(
        self,
        endpoint: str,
        ok: bool,
        status: Optional[int],
        latency_ms: Optional[float],
        blocked: bool = False,
        tiles: Optional[int] = None,
        error: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO polls (endpoint, ts, ok, status, latency_ms, blocked, tiles, error)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (endpoint, now or time.time(), int(ok), status, latency_ms, int(blocked), tiles, error),
        )
        self._conn.commit()

    def poll_stats(self, window_h: float = 72.0, now: Optional[float] = None) -> dict:
        """Gate 1.1 / 1.2 numbers: success rate, block events, latency."""
        now = now or time.time()
        cur = self._conn.execute(
            "SELECT COUNT(*), SUM(ok), SUM(blocked), AVG(latency_ms)"
            " FROM polls WHERE ts > ?",
            (now - window_h * 3600,),
        )
        total, ok, blocked, avg_latency = cur.fetchone()
        return {
            "window_h": window_h,
            "polls": total or 0,
            "success_rate": (ok / total) if total else None,
            "block_events": blocked or 0,
            "avg_latency_ms": avg_latency,
        }

    def summary(self) -> dict:
        sightings = self._conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
        alerts = self._conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        return {"sightings": sightings, "alerts": alerts, **self.poll_stats()}
