"""CLI — every subcommand maps onto a success gate.

  parse       gate 0.1: extract tiles from a captured HAR/HTML, fail on errors
  probe       gate 0.4: N live fetches from this machine, report status mix
  targets     dry-run the matcher against a HAR/HTML capture
  test-alert  verify Telegram/Twilio delivery end to end
  inject      gate 1.3: drop a synthetic matching tile into a running watcher
  watch       run the detection loop
  status      gates 1.1/1.2: poll success rate, block events, alert history
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .matcher import SniperConfig, match_tiles
from .notify import Notifier
from .parser import ParseReport, parse_har, parse_html
from .state import StateDB
from .transport import fetch, have_impersonation

DEFAULT_STATE_DIR = Path.home() / ".mac_studio_sniper"
DEFAULT_TARGETS = Path(__file__).parent / "targets.yaml"


def _load_capture(args: argparse.Namespace) -> ParseReport:
    if args.har:
        return parse_har(Path(args.har).read_text(encoding="utf-8"))
    return parse_html(Path(args.html).read_text(encoding="utf-8"), source=args.html)


def _print_tiles(report: ParseReport) -> None:
    for t in sorted(report.tiles, key=lambda t: t.part_number):
        price = f"${t.price_usd:,.2f}" if t.price_usd is not None else "?"
        ram = f"{t.ram_gb}GB" if t.ram_gb else "?"
        print(f"  {t.part_number:14s} {t.chip or '?':9s} ram={ram:6s} {price:>11s}  {t.title}")


def cmd_parse(args: argparse.Namespace) -> int:
    report = _load_capture(args)
    print(f"tiles: {len(report.tiles)}   json blobs scanned: {report.json_blobs_scanned}")
    _print_tiles(report)
    for err in report.errors:
        print(f"ERROR: {err}", file=sys.stderr)
    # Gate 0.1 is pass/fail on this exit code.
    return 1 if report.errors else 0


def cmd_targets(args: argparse.Namespace) -> int:
    config = SniperConfig.load(Path(args.targets))
    report = _load_capture(args)
    matches = match_tiles(report.tiles, config)
    print(f"tiles: {len(report.tiles)}, matches: {len(matches)}")
    for m in matches:
        print("  " + m.headline())
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Gate 0.4: N sequential fetches; report status/block/latency mix."""
    print(f"transport: {'curl_cffi (chrome impersonation)' if have_impersonation() else 'httpx (install curl_cffi for TLS impersonation)'}")
    ok = blocked = 0
    for i in range(args.count):
        r = fetch(args.url)
        tiles = len(parse_html(r.text).tiles) if r.ok else 0
        print(
            f"[{i + 1}/{args.count}] status={r.status} ok={r.ok} blocked={r.blocked}"
            f" latency={r.latency_ms:.0f}ms tiles={tiles} {r.error or ''}"
        )
        ok += r.ok and tiles > 0
        blocked += r.blocked
        if i + 1 < args.count:
            time.sleep(args.interval)
    print(f"\nresult: {ok}/{args.count} clean parses, {blocked} block events")
    passed = ok == args.count
    print(f"gate 0.4: {'PASS' if passed else 'FAIL'} (requires {args.count}/{args.count})")
    return 0 if passed else 1


def cmd_test_alert(args: argparse.Namespace) -> int:
    notifier = Notifier()
    delivered = notifier.send_raw(
        "✅ mac_studio_sniper test alert — if you can read this on your phone,"
        " the alert path works."
    )
    print(f"delivered via: {delivered}")
    want = set(notifier.channels())
    return 0 if want.issubset(set(delivered)) else 1


def cmd_inject(args: argparse.Namespace) -> int:
    inject_dir = Path(args.state_dir) / "inject"
    inject_dir.mkdir(parents=True, exist_ok=True)
    tile = {
        "part_number": args.part or f"GTEST{int(time.time()) % 100000}/A",
        "title": args.title,
        "price_usd": args.price,
        "ram_gb": args.ram,
        "url": "https://www.apple.com/shop/refurbished/mac/mac-studio",
    }
    out = inject_dir / f"inject-{int(time.time() * 1000)}.json"
    out.write_text(json.dumps([tile]), encoding="utf-8")
    print(f"injected {tile['part_number']} → {out}")
    print("(measure gate 1.3 as: time from this command to phone notification)")
    return 0


def cmd_recon_grid(args: argparse.Namespace) -> int:
    from .recon import run_grid_recon

    return run_grid_recon(
        url=args.url, out_dir=Path(args.out_dir), executable_path=args.browser_path
    )


def cmd_recon_checkout(args: argparse.Namespace) -> int:
    from .recon import run_checkout_recon

    return run_checkout_recon(
        out_dir=Path(args.out_dir),
        profile_dir=Path(args.profile_dir),
        start_url=args.start_url,
        executable_path=args.browser_path,
    )


def cmd_telegram_setup(args: argparse.Namespace) -> int:
    import os

    from .recon import run_telegram_setup

    token = args.token or os.environ.get("SNIPER_TELEGRAM_BOT_TOKEN")
    if not token:
        print("pass --token or set SNIPER_TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 1
    return run_telegram_setup(token)


def cmd_status(args: argparse.Namespace) -> int:
    db = StateDB(Path(args.state_dir) / "state.sqlite")
    summary = db.summary()
    rate = summary["success_rate"]
    print(f"sightings: {summary['sightings']}   alerts: {summary['alerts']}")
    print(
        f"polls({summary['window_h']:.0f}h): {summary['polls']}"
        f"   success rate: {rate:.4f}" if rate is not None else "   success rate: n/a"
    )
    print(f"block events: {summary['block_events']}   avg latency: "
          f"{summary['avg_latency_ms']:.0f}ms" if summary["avg_latency_ms"] else "block events: 0")
    if rate is not None:
        print(f"gate 1.1 (≥0.99): {'PASS' if rate >= 0.99 else 'FAIL'}"
              f"   gate 1.2 (0 blocks): {'PASS' if summary['block_events'] == 0 else 'FAIL'}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    import asyncio

    from .watcher import Watcher

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    state_dir = Path(args.state_dir)
    config = SniperConfig.load(Path(args.targets))
    if config.mode not in ("alert-only",):
        print(
            f"mode '{config.mode}' requested but the buyer is not implemented yet"
            " (Phase 2) — running alert-only.",
            file=sys.stderr,
        )
    watcher = Watcher(
        config=config,
        state=StateDB(state_dir / "state.sqlite"),
        notifier=Notifier(),
        state_dir=state_dir,
    )
    try:
        asyncio.run(watcher.run())
    except KeyboardInterrupt:
        print("watcher stopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mac_studio_sniper")
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_capture_args(sp: argparse.ArgumentParser) -> None:
        g = sp.add_mutually_exclusive_group(required=True)
        g.add_argument("--har", help="HAR file captured from DevTools")
        g.add_argument("--html", help="Saved grid page HTML")

    sp = sub.add_parser("parse", help="gate 0.1: extract tiles from a capture")
    add_capture_args(sp)
    sp.set_defaults(fn=cmd_parse)

    sp = sub.add_parser("targets", help="dry-run matcher against a capture")
    add_capture_args(sp)
    sp.add_argument("--targets", default=str(DEFAULT_TARGETS))
    sp.set_defaults(fn=cmd_targets)

    sp = sub.add_parser("probe", help="gate 0.4: live transport viability check")
    sp.add_argument("--url", default="https://www.apple.com/shop/refurbished/mac/mac-studio")
    sp.add_argument("--count", type=int, default=20)
    sp.add_argument("--interval", type=float, default=180.0, help="seconds between fetches")
    sp.set_defaults(fn=cmd_probe)

    sp = sub.add_parser("test-alert", help="send a test alert on all channels")
    sp.set_defaults(fn=cmd_test_alert)

    sp = sub.add_parser("inject", help="gate 1.3: synthetic drop into a running watcher")
    sp.add_argument("--title", default="Refurbished Mac Studio Apple M3 Ultra Chip with 96GB memory (SYNTHETIC)")
    sp.add_argument("--price", type=float, default=4599.0)
    sp.add_argument("--ram", type=int, default=None)
    sp.add_argument("--part", default=None)
    sp.set_defaults(fn=cmd_inject)

    from .recon import DEFAULT_RECON_DIR, GRID_URL

    sp = sub.add_parser("recon-grid", help="gates 0.1+0.3: automated grid capture & parse")
    sp.add_argument("--url", default=GRID_URL)
    sp.add_argument("--out-dir", default=str(DEFAULT_RECON_DIR))
    sp.add_argument("--browser-path", default=None, help="chromium executable override")
    sp.set_defaults(fn=cmd_recon_grid)

    sp = sub.add_parser(
        "recon-checkout",
        help="gate 0.2: record selectors automatically while you walk one checkout",
    )
    sp.add_argument("--out-dir", default=str(DEFAULT_RECON_DIR))
    sp.add_argument(
        "--profile-dir", default=str(Path.home() / ".mac_studio_sniper" / "browser-profile")
    )
    sp.add_argument("--start-url", default="https://www.apple.com/shop/refurbished")
    sp.add_argument("--browser-path", default=None)
    sp.set_defaults(fn=cmd_recon_checkout)

    sp = sub.add_parser("telegram-setup", help="discover chat id from bot token + test send")
    sp.add_argument("--token", default=None)
    sp.set_defaults(fn=cmd_telegram_setup)

    sp = sub.add_parser("status", help="gates 1.1/1.2: metrics from the state DB")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("watch", help="run the detection loop")
    sp.add_argument("--targets", default=str(DEFAULT_TARGETS))
    sp.set_defaults(fn=cmd_watch)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
