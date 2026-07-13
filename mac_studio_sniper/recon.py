"""Automated G0 recon (runs on the operator's home machine).

Collapses the manual RECON.md checklist into three commands:

  recon-grid       gate 0.1 + 0.3 — Playwright loads the refurb grid in a
                   fresh logged-out context, records HAR + HTML, runs the
                   parser, prints the gate verdict and suggested price caps.
  recon-checkout   gate 0.2 — opens a headed browser with a persistent
                   profile and records, automatically, every element you
                   click (tag, id, data-autom, text, sanitized outerHTML)
                   plus a HAR, while YOU walk one checkout manually. Output
                   is the checkout-selectors file that seeds flightplan.yaml.
  telegram-setup   discovers your chat id from the bot token and sends a
                   test alert.

The only human-only steps left: Apple ID sign-in (2FA) and physically
clicking through the checkout once.

All captures land under ``~/.mac_studio_sniper/recon/`` — OUTSIDE the repo
— because HARs from authenticated sessions contain cookies and PII and
must never be committed. Input *values* are never recorded by the click
instrumentation, only element structure.

Requires: ``pip install playwright && playwright install chromium``.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any, Optional

from .parser import parse_html
from .models import Tile

GRID_URL = "https://www.apple.com/shop/refurbished/mac/mac-studio"
DEFAULT_RECON_DIR = Path.home() / ".mac_studio_sniper" / "recon"

# Injected into every page of the recording context. Captures click/submit
# targets WITHOUT input values (PII/card safety): outerHTML is taken from a
# shallow clone with value/data-value attributes stripped.
_CLICK_RECORDER_JS = """
(() => {
  // Guard on document, not window: the window object can survive a
  // navigation whose new document has no listeners yet (observed with
  // Playwright set_content; also SPA edge cases).
  if (document.__sniperInstalled) return;
  document.__sniperInstalled = true;
  const describe = (el) => {
    if (!el || !el.tagName) return null;
    let clone = null;
    try {
      clone = el.cloneNode(false);
      clone.removeAttribute && clone.removeAttribute('value');
      clone.removeAttribute && clone.removeAttribute('data-value');
    } catch (e) {}
    const text = (el.innerText || '').trim().slice(0, 80);
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      name: el.getAttribute('name'),
      type: el.getAttribute('type'),
      dataAutom: el.getAttribute('data-autom'),
      ariaLabel: el.getAttribute('aria-label'),
      classes: (el.className && el.className.baseVal === undefined ? el.className : '')
        .split(/\\s+/).filter(Boolean).slice(0, 6),
      text,
      outerHtml: clone ? clone.outerHTML.slice(0, 400) : null,
    };
  };
  const record = (kind, el) => {
    const target = el && el.closest
      ? (el.closest('button, a, input, select, [role=button], [data-autom]') || el)
      : el;
    const info = describe(target);
    if (!info) return;
    try {
      window.__sniperRecord(JSON.stringify({
        kind, ts: Date.now() / 1000, url: location.href, element: info,
      }));
    } catch (e) {}
  };
  document.addEventListener('click', (e) => record('click', e.target), true);
  document.addEventListener('submit', (e) => record('submit', e.target), true);
})();
"""


def _require_playwright():
    try:
        from playwright.async_api import async_playwright  # noqa: F401

        return async_playwright
    except ImportError as e:  # pragma: no cover - environment dependent
        raise SystemExit(
            "playwright is required for recon commands:\n"
            "  pip install playwright && playwright install chromium"
        ) from e


# ---------------------------------------------------------------------------
# Gate 0.1 + 0.3: grid capture
# ---------------------------------------------------------------------------


def suggest_price_caps(tiles: list[Tile]) -> list[str]:
    """Turn observed M3 Ultra prices into suggested targets.yaml caps."""
    lines: list[str] = []
    ultras = [t for t in tiles if t.chip == "M3 Ultra" and t.price_usd is not None]
    if not ultras:
        return [
            "no M3 Ultra listings observed right now — keep the placeholder caps",
            "and re-run recon-grid when one appears, or use list price × 0.88.",
        ]
    for ram in (512, 256):
        prices = [t.price_usd for t in ultras if t.ram_gb == ram]
        if prices:
            cap = round(max(prices) * 1.02 / 50) * 50
            lines.append(
                f"M3 Ultra {ram}GB: observed {len(prices)} listing(s), "
                f"min ${min(prices):,.0f} / median ${statistics.median(prices):,.0f} / "
                f"max ${max(prices):,.0f} → suggested max_price_usd: {cap}"
            )
    unknown = [t for t in ultras if t.ram_gb not in (256, 512)]
    if unknown:
        lines.append(
            f"({len(unknown)} M3 Ultra listing(s) with other/unknown RAM observed — "
            "prices logged in the capture for reference)"
        )
    return lines


async def _grid_recon_async(url: str, out_dir: Path, executable_path: Optional[str]) -> int:
    async_playwright = _require_playwright()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    har_path = out_dir / f"grid-{stamp}.har"
    html_path = out_dir / f"grid-{stamp}.html"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, executable_path=executable_path or None
        )
        # Fresh context = logged out, per gate 0.1.
        context = await browser.new_context(record_har_path=str(har_path))
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass  # networkidle is best-effort; the embedded JSON is server-rendered
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        await context.close()  # flushes the HAR
        await browser.close()

    report = parse_html(html, source=url)
    print(f"captured: {html_path}")
    print(f"          {har_path}  (contains no login cookies — logged-out context)")
    print(f"tiles: {len(report.tiles)}   errors: {len(report.errors)}")
    for t in sorted(report.tiles, key=lambda t: (t.chip or "", t.part_number)):
        price = f"${t.price_usd:,.2f}" if t.price_usd is not None else "?"
        print(f"  {t.part_number:14s} {t.chip or '?':9s} ram={t.ram_gb or '?':>4} {price:>11s}  {t.title[:70]}")
    for err in report.errors:
        print(f"ERROR: {err}")
    print("\n--- gate 0.1:", "PASS (verify the count matches what the page shows!)" if not report.errors else "FAIL")
    print("--- gate 0.3 price-cap suggestions:")
    for line in suggest_price_caps(report.tiles):
        print("  " + line)
    return 1 if report.errors else 0


def run_grid_recon(
    url: str = GRID_URL,
    out_dir: Path = DEFAULT_RECON_DIR,
    executable_path: Optional[str] = None,
) -> int:
    return asyncio.run(_grid_recon_async(url, out_dir, executable_path))


# ---------------------------------------------------------------------------
# Gate 0.2: instrumented checkout walk
# ---------------------------------------------------------------------------


def format_selector_report(events: list[dict[str, Any]]) -> str:
    """Human-readable checkout-selectors report from recorded events."""
    lines = [
        "# Checkout selector recording",
        "# One block per interaction, in order. data-autom attributes are",
        "# Apple's own test hooks — prefer them as primary selectors.",
        "",
    ]
    last_url = None
    step = 0
    for ev in events:
        el = ev.get("element") or {}
        if ev.get("url") != last_url:
            last_url = ev.get("url")
            lines.append(f"\n== PAGE: {last_url}")
        step += 1
        sel = None
        if el.get("dataAutom"):
            sel = f"[data-autom=\"{el['dataAutom']}\"]"
        elif el.get("id"):
            sel = f"#{el['id']}"
        elif el.get("name"):
            sel = f"{el.get('tag', '*')}[name=\"{el['name']}\"]"
        lines.append(
            f"[{step:02d}] {ev.get('kind', '?'):6s} <{el.get('tag')}> "
            f"text={el.get('text')!r} suggested_selector={sel or 'NEEDS MANUAL PICK'}"
        )
        if el.get("outerHtml"):
            lines.append(f"     html: {el['outerHtml']}")
    return "\n".join(lines) + "\n"


async def _checkout_recon_async(
    out_dir: Path, profile_dir: Path, start_url: str, executable_path: Optional[str]
) -> int:
    async_playwright = _require_playwright()
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    har_path = out_dir / f"checkout-{stamp}.har"
    events_path = out_dir / f"checkout-{stamp}.events.jsonl"
    report_path = out_dir / f"checkout-selectors-{stamp}.txt"

    events: list[dict[str, Any]] = []
    events_file = events_path.open("a", encoding="utf-8")

    async def on_record(_source, payload: str) -> None:
        try:
            ev = json.loads(payload)
        except json.JSONDecodeError:
            return
        events.append(ev)
        events_file.write(json.dumps(ev) + "\n")
        events_file.flush()
        el = ev.get("element") or {}
        print(f"  recorded {ev.get('kind')}: <{el.get('tag')}> "
              f"data-autom={el.get('dataAutom')} text={el.get('text')!r}")

    print(
        "\nA browser window will open with a persistent profile (your Apple ID\n"
        "login is kept for the watcher/buyer to reuse). Now:\n"
        "  1. sign in to apple.com (2FA — this is the one human-only bit)\n"
        "  2. pick the CHEAPEST in-stock refurb item\n"
        "  3. walk: Add to Bag → Check Out → shipping → payment\n"
        "  4. STOP on the Place Order page — do NOT click Place Order\n"
        "  5. close the browser window to finish recording\n"
        "Every click is recorded automatically; input values are NOT captured.\n"
    )

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            executable_path=executable_path or None,
            record_har_path=str(har_path),
            viewport={"width": 1440, "height": 900},
        )
        await context.expose_binding("__sniperRecord", on_record)
        await context.add_init_script(_CLICK_RECORDER_JS)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
        try:
            await context.wait_for_event("close", timeout=0)  # until user closes
        except Exception:
            pass
    events_file.close()

    report_path.write_text(format_selector_report(events), encoding="utf-8")
    print(f"\nrecorded {len(events)} interactions")
    print(f"selector report: {report_path}   <-- share THIS (no PII)")
    print(f"events:          {events_path}")
    print(f"HAR:             {har_path}   <-- contains cookies/PII, NEVER share or commit")
    return 0


def run_checkout_recon(
    out_dir: Path = DEFAULT_RECON_DIR,
    profile_dir: Path = Path.home() / ".mac_studio_sniper" / "browser-profile",
    start_url: str = "https://www.apple.com/shop/refurbished",
    executable_path: Optional[str] = None,
) -> int:
    return asyncio.run(_checkout_recon_async(out_dir, profile_dir, start_url, executable_path))


# ---------------------------------------------------------------------------
# Telegram bootstrap
# ---------------------------------------------------------------------------


def run_telegram_setup(token: str) -> int:
    """Discover the chat id (user must have messaged the bot once) and test."""
    import httpx

    resp = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    if resp.status_code != 200:
        print(f"getUpdates failed: HTTP {resp.status_code} {resp.text[:200]}")
        return 1
    chats = {}
    for upd in resp.json().get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            chats[chat["id"]] = chat.get("username") or chat.get("first_name") or "?"
    if not chats:
        print(
            "no chats found — open Telegram, send your bot any message"
            " (e.g. 'hi'), then re-run this command."
        )
        return 1
    chat_id = list(chats)[0]
    print(f"found chat(s): {chats} — using {chat_id}")
    send = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "✅ mac_studio_sniper connected. Alerts will arrive here."},
        timeout=15,
    )
    if send.status_code != 200:
        print(f"test send failed: {send.text[:200]}")
        return 1
    print("\ntest message sent. Add to the watcher's environment:")
    print(f"  export SNIPER_TELEGRAM_BOT_TOKEN={token[:6]}…   (your full token)")
    print(f"  export SNIPER_TELEGRAM_CHAT_ID={chat_id}")
    return 0
