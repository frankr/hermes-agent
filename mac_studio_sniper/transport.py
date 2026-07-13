"""HTTP transport for polling apple.com behind Akamai Bot Manager.

Ladder (per the design doc):
  1. ``curl_cffi`` with Chrome TLS impersonation — cheap and fingerprint-clean.
  2. ``httpx`` with browser-like headers — works from residential IPs when
     Akamai is lenient; the fallback when curl_cffi isn't installed.
  3. (Phase 2) fetch() inside the warmed real-browser page.

Every response is classified so the watcher can record block events
(gate 1.2) distinctly from ordinary failures.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Optional

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_BLOCK_MARKERS = (
    "access denied",
    "/aka-challenge",
    "bot detection",
    "reference #",  # Akamai denial pages carry "Reference #xx.xxxx"
)

try:  # optional dependency — the preferred transport
    from curl_cffi import requests as _curl_requests  # type: ignore

    _HAVE_CURL_CFFI = True
except Exception:  # pragma: no cover - environment dependent
    _curl_requests = None
    _HAVE_CURL_CFFI = False


@dataclass
class FetchResult:
    ok: bool
    status: Optional[int]
    text: str
    latency_ms: float
    blocked: bool
    transport: str
    error: Optional[str] = None
    not_modified: bool = False
    etag: Optional[str] = None


def have_impersonation() -> bool:
    return _HAVE_CURL_CFFI


def classify_block(status: Optional[int], text: str) -> bool:
    if status in (403, 429):
        return True
    if status == 200 and text:
        lowered = text[:4000].lower()
        return any(marker in lowered for marker in _BLOCK_MARKERS)
    return False


def fetch(url: str, etag: Optional[str] = None, timeout_s: float = 20.0) -> FetchResult:
    headers = dict(_BROWSER_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    start = time.monotonic()
    try:
        if _HAVE_CURL_CFFI:
            resp = _curl_requests.get(
                url, headers=headers, impersonate="chrome", timeout=timeout_s
            )
            transport = "curl_cffi"
        else:
            import httpx

            resp = httpx.get(url, headers=headers, timeout=timeout_s, follow_redirects=True)
            transport = "httpx"
    except Exception as e:  # network errors, TLS, proxy…
        return FetchResult(
            ok=False,
            status=None,
            text="",
            latency_ms=(time.monotonic() - start) * 1000,
            blocked=False,
            transport="curl_cffi" if _HAVE_CURL_CFFI else "httpx",
            error=f"{type(e).__name__}: {e}",
        )
    latency_ms = (time.monotonic() - start) * 1000
    status = resp.status_code
    if status == 304:
        return FetchResult(
            ok=True,
            status=304,
            text="",
            latency_ms=latency_ms,
            blocked=False,
            transport=transport,
            not_modified=True,
            etag=etag,
        )
    text = resp.text or ""
    blocked = classify_block(status, text)
    return FetchResult(
        ok=(status == 200 and not blocked),
        status=status,
        text=text,
        latency_ms=latency_ms,
        blocked=blocked,
        transport=transport,
        error=None if status == 200 else f"HTTP {status}",
        etag=resp.headers.get("ETag"),
    )


def jittered_interval(base_s: float, jitter_frac: float = 0.35) -> float:
    """Humans don't poll on a metronome; neither do we."""
    return base_s * (1 + random.uniform(-jitter_frac, jitter_frac))
