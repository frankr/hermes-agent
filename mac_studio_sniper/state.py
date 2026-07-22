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
CREATE TABLE IF NOT EXISTS drills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    mode        TEXT NOT NULL,           -- drill | live
    ok          INTEGER NOT NULL,
    duration_ms REAL,
    failed_step TEXT,
    notes       TEXT
);
CREATE TABLE IF NOT EXISTS purchases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    part_number TEXT NOT NULL,
    price_usd   REAL,
    order_ref   TEXT,
    mode        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    kind   TEXT NOT NULL,                -- session | heartbeat
    ts     REAL NOT NULL,
    ok     INTEGER NOT NULL,
    notes  TEXT
);
CREATE TABLE IF NOT EXISTS race_ready (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    ready   INTEGER NOT NULL,
    reasons TEXT
);
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

    def sightings_matching(self, chip_substr: Optional[str] = None) -> list[dict]:
        """All sightings, optionally filtered to a chip substring (e.g. 'M3 Ultra').

        Answers the 'is it even showing up?' question directly from the log —
        no matching / price caps involved. Newest first_seen last.
        """
        rows = self._conn.execute(
            "SELECT part_number, title, chip, ram_gb, price_usd, url,"
            " first_seen, last_seen, times_seen FROM sightings ORDER BY first_seen"
        ).fetchall()
        out = []
        for r in rows:
            chip = r[2] or ""
            if chip_substr and chip_substr.lower() not in chip.lower():
                continue
            out.append(
                {
                    "part_number": r[0],
                    "title": r[1],
                    "chip": r[2],
                    "ram_gb": r[3],
                    "price_usd": r[4],
                    "url": r[5],
                    "first_seen": r[6],
                    "last_seen": r[7],
                    "times_seen": r[8],
                }
            )
        return out

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

    # -- drills / purchases / checks (Phase 2+) -----------------------------

    def record_drill(
        self,
        mode: str,
        ok: bool,
        duration_ms: Optional[float] = None,
        failed_step: Optional[str] = None,
        notes: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO drills (ts, mode, ok, duration_ms, failed_step, notes)"
            " VALUES (?,?,?,?,?,?)",
            (now or time.time(), mode, int(ok), duration_ms, failed_step, notes),
        )
        self._conn.commit()

    def last_passing_drill_age_h(self, now: Optional[float] = None) -> Optional[float]:
        row = self._conn.execute(
            "SELECT MAX(ts) FROM drills WHERE ok = 1 AND mode = 'drill'"
        ).fetchone()
        if not row or row[0] is None:
            return None
        return ((now or time.time()) - row[0]) / 3600

    def consecutive_passing_drills(self) -> int:
        """Gate 2.1: passing streak of the most recent drills."""
        streak = 0
        for (ok,) in self._conn.execute(
            "SELECT ok FROM drills WHERE mode = 'drill' ORDER BY ts DESC"
        ):
            if not ok:
                break
            streak += 1
        return streak

    def record_purchase(
        self,
        part_number: str,
        price_usd: Optional[float],
        order_ref: Optional[str],
        mode: str,
        now: Optional[float] = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO purchases (ts, part_number, price_usd, order_ref, mode)"
            " VALUES (?,?,?,?,?)",
            (now or time.time(), part_number, price_usd, order_ref, mode),
        )
        self._conn.commit()

    def purchase_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]

    def record_check(
        self, kind: str, ok: bool, notes: Optional[str] = None, now: Optional[float] = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO checks (kind, ts, ok, notes) VALUES (?,?,?,?)",
            (kind, now or time.time(), int(ok), notes),
        )
        self._conn.commit()

    def last_check(self, kind: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT ts, ok, notes FROM checks WHERE kind = ? ORDER BY ts DESC LIMIT 1",
            (kind,),
        ).fetchone()
        if row is None:
            return None
        return {"ts": row[0], "ok": bool(row[1]), "notes": row[2]}

    def last_poll_ts(self) -> Optional[float]:
        row = self._conn.execute("SELECT MAX(ts) FROM polls WHERE ok = 1").fetchone()
        return row[0] if row else None

    def first_poll_ts(self) -> Optional[float]:
        row = self._conn.execute("SELECT MIN(ts) FROM polls WHERE ok = 1").fetchone()
        return row[0] if row else None

    def record_race_ready(
        self, ready: bool, reasons: list[str], now: Optional[float] = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO race_ready (ts, ready, reasons) VALUES (?,?,?)",
            (now or time.time(), int(ready), "; ".join(reasons)),
        )
        self._conn.commit()

    def race_ready_rate(self, window_h: float = 168.0, now: Optional[float] = None) -> Optional[float]:
        """The operational SLO number (target ≥ 0.95)."""
        now = now or time.time()
        row = self._conn.execute(
            "SELECT COUNT(*), SUM(ready) FROM race_ready WHERE ts > ?",
            (now - window_h * 3600,),
        ).fetchone()
        total, ready = row
        return (ready / total) if total else None

    def summary(self) -> dict:
        sightings = self._conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
        alerts = self._conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        drill_age = self.last_passing_drill_age_h()
        return {
            "sightings": sightings,
            "alerts": alerts,
            "purchases": self.purchase_count(),
            "drill_streak": self.consecutive_passing_drills(),
            "last_passing_drill_age_h": drill_age,
            "race_ready_rate_7d": self.race_ready_rate(),
            **self.poll_stats(),
        }
