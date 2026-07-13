"""Drop-window learning (gate 4.4).

Learns which local-time hours refurb inventory actually appears from the
``sightings.first_seen`` history, so the watcher tightens cadence when a
drop is statistically likely instead of guessing. Pure function over the
state DB — the supervisor calls ``learn_hot_hours`` periodically and writes
the result back into the watcher's config.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def observed_first_seen_hours(db_path: Path) -> list[int]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT first_seen FROM sightings").fetchall()
    finally:
        conn.close()
    return [datetime.fromtimestamp(ts).hour for (ts,) in rows if ts]


def learn_hot_hours(hours: list[int], min_samples: int = 5) -> list[list[int]]:
    """Return merged [start, end) local-hour windows covering drop-heavy hours.

    An hour is "hot" if it saw at least the mean per-hour count (and there
    are enough samples to be meaningful). Adjacent hot hours merge into
    ranges. Falls back to the historical early-morning default when data is
    thin.
    """
    if len(hours) < min_samples:
        return [[4, 8]]
    counts = [0] * 24
    for h in hours:
        counts[h] += 1
    active = [c for c in counts if c > 0]
    threshold = max(1, sum(active) / len(active))  # mean over hours that fired
    hot = sorted(h for h in range(24) if counts[h] >= threshold)
    if not hot:
        return [[4, 8]]
    windows: list[list[int]] = []
    start = prev = hot[0]
    for h in hot[1:]:
        if h == prev + 1:
            prev = h
        else:
            windows.append([start, prev + 1])
            start = prev = h
    windows.append([start, prev + 1])
    return windows
