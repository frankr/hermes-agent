"""CLI — every subcommand maps onto a success gate.

  parse       gate 0.1: extract tiles from a captured HAR/HTML, fail on errors
  probe       gate 0.4: N live fetches from this machine, report status mix
  targets     dry-run the matcher against a HAR/HTML capture
  test-alert  verify Telegram/Twilio delivery end to end
  inject      gate 1.3: drop a synthetic matching tile into a running watcher
  watch       run the detection loop (wires the buyer when mode != alert-only)
  status      gates 1.1/1.2: poll success rate, block events, alert history
  drill       gates 2.1/2.2: rehearse the strike path up to the final button
  session-check  gate 2.3/4.2: verify the Apple ID session is still valid
  heartbeat   gate 4.1: watcher liveness
  race-ready  compute + log the operational SLO snapshot
  flightplan  validate flightplan.yaml, show verified/placeholder status
  learn-windows  gate 4.4: recompute hot hours from sighting history
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
DEFAULT_FLIGHTPLAN = Path(__file__).parent / "flightplan.yaml"
DEFAULT_PROFILE = Path.home() / ".mac_studio_sniper" / "browser-profile"


def _build_buyer(args: argparse.Namespace, config, state):
    from .buyer import Buyer
    from .flightplan import Flightplan
    from .interact import build_interactor

    state_dir = Path(args.state_dir)
    return Buyer(
        config=config,
        flightplan=Flightplan.load(Path(args.flightplan)),
        state=state,
        notifier=Notifier(),
        interactor=build_interactor(state_dir),
        state_dir=state_dir,
        profile_dir=Path(getattr(args, "profile_dir", DEFAULT_PROFILE)),
        browser_path=getattr(args, "browser_path", None),
        headless=not getattr(args, "headed", False),
    )


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


def cmd_report(args: argparse.Namespace) -> int:
    """Availability report: has the target chip shown up, and how often."""
    import time as _time

    db = StateDB(Path(args.state_dir) / "state.sqlite")
    sightings = db.sightings_matching(chip_substr=args.chip)
    label = args.chip or "all"
    if not sightings:
        print(f"No {label} sightings recorded yet.")
        print("Keep the watcher running: python -m mac_studio_sniper watch \\")
        print("    --targets mac_studio_sniper/targets.availability.yaml")
        return 0
    now = _time.time()
    print(f"{label} sightings: {len(sightings)} distinct SKU(s)\n")
    print(f"  {'part':14s} {'RAM':>5s} {'price':>10s}  {'seen':>4s}  first → last")
    for s in sightings:
        ram = f"{s['ram_gb']}GB" if s["ram_gb"] else "?"
        price = f"${s['price_usd']:,.0f}" if s["price_usd"] is not None else "?"
        first = _time.strftime("%m-%d %H:%M", _time.localtime(s["first_seen"]))
        last = _time.strftime("%m-%d %H:%M", _time.localtime(s["last_seen"]))
        age_h = (now - s["last_seen"]) / 3600
        still = " (on page now)" if age_h < 0.2 else ""
        print(f"  {s['part_number']:14s} {ram:>5s} {price:>10s}  {s['times_seen']:>4d}  {first} → {last}{still}")
    # Cadence summary.
    firsts = sorted(s["first_seen"] for s in sightings)
    span_days = (firsts[-1] - firsts[0]) / 86400 if len(firsts) > 1 else 0
    watching_days = (now - db.first_poll_ts()) / 86400 if db.first_poll_ts() else 0
    print(
        f"\n{len(firsts)} distinct SKU(s) first appeared over {span_days:.1f} day(s);"
        f" watching for {watching_days:.1f} day(s)."
    )
    ultra_512 = [s for s in sightings if s["ram_gb"] == 512]
    ultra_256 = [s for s in sightings if s["ram_gb"] == 256]
    print(f"  512GB seen: {len(ultra_512)} SKU(s)   256GB seen: {len(ultra_256)} SKU(s)")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    import asyncio

    from .watcher import Watcher

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    state_dir = Path(args.state_dir)
    config = SniperConfig.load(Path(args.targets))
    state = StateDB(state_dir / "state.sqlite")

    on_match = None
    if config.mode in ("confirm", "full-auto"):
        buyer = _build_buyer(args, config, state)

        async def on_match(match):
            # The buyer re-checks every guardrail at strike time; a blocked
            # arm degrades to alert-only (the alert already fired).
            await buyer.attempt_purchase(match)

        print(f"buyer armed in {config.mode!r} mode (guardrails enforced at strike time)")
    else:
        print(f"mode {config.mode!r}: alert-only, buyer not wired")

    watcher = Watcher(
        config=config,
        state=state,
        notifier=Notifier(),
        state_dir=state_dir,
        on_match=on_match,
    )
    try:
        asyncio.run(watcher.run())
    except KeyboardInterrupt:
        print("watcher stopped")
    return 0


def cmd_drill(args: argparse.Namespace) -> int:
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = SniperConfig.load(Path(args.targets))
    state = StateDB(Path(args.state_dir) / "state.sqlite")
    buyer = _build_buyer(args, config, state)
    url = args.url or buyer.flightplan.drill_grid_url
    if not url:
        print("no drill URL: pass --url or set drill_grid_url in flightplan.yaml", file=sys.stderr)
        return 1
    result = asyncio.run(buyer.drill(url))
    print(f"drill {'PASS' if result.ok else 'FAIL'} in {result.duration_ms:.0f}ms")
    if result.step_timings_ms:
        for sid, ms in result.step_timings_ms.items():
            print(f"  {sid:22s} {ms:7.0f}ms")
    if not result.ok:
        print(f"  failed at {result.failed_step}: {result.error}", file=sys.stderr)
        print(f"  artifacts: {result.artifacts_dir}", file=sys.stderr)
    streak = state.consecutive_passing_drills()
    print(f"consecutive passing drills: {streak}  (gate 2.1 needs 7)")
    return 0 if result.ok else 1


def cmd_session_check(args: argparse.Namespace) -> int:
    import asyncio

    from .supervisor import Supervisor
    from .flightplan import Flightplan

    config = SniperConfig.load(Path(args.targets))
    state = StateDB(Path(args.state_dir) / "state.sqlite")
    sup = Supervisor(
        config, Flightplan.load(Path(args.flightplan)), state, Notifier(), Path(args.state_dir)
    )
    ok = asyncio.run(sup.session_check(Path(args.profile_dir), browser_path=args.browser_path))
    print(f"session check: {'VALID' if ok else 'INVALID'}")
    return 0 if ok else 1


def cmd_heartbeat(args: argparse.Namespace) -> int:
    from .supervisor import Supervisor
    from .flightplan import Flightplan

    config = SniperConfig.load(Path(args.targets))
    state = StateDB(Path(args.state_dir) / "state.sqlite")
    sup = Supervisor(
        config, Flightplan.load(Path(args.flightplan)), state, Notifier(), Path(args.state_dir)
    )
    ok = sup.heartbeat()
    print(f"heartbeat: {'OK' if ok else 'STALE'}")
    return 0 if ok else 1


def cmd_race_ready(args: argparse.Namespace) -> int:
    from .supervisor import Supervisor
    from .flightplan import Flightplan

    config = SniperConfig.load(Path(args.targets))
    state = StateDB(Path(args.state_dir) / "state.sqlite")
    sup = Supervisor(
        config, Flightplan.load(Path(args.flightplan)), state, Notifier(), Path(args.state_dir)
    )
    rr = sup.race_ready_snapshot()
    print(rr.render())
    rate = state.race_ready_rate()
    if rate is not None:
        print(f"7-day race-ready rate: {rate:.3f}  (SLO ≥ 0.95: {'PASS' if rate >= 0.95 else 'FAIL'})")
    return 0 if rr.ready else 1


def cmd_flightplan(args: argparse.Namespace) -> int:
    from .flightplan import Flightplan

    fp = Flightplan.load(Path(args.flightplan))
    print(f"version: {fp.version}   verified: {fp.verified}")
    print(f"steps: {len(fp.steps)}   final step: {fp.final_step.id if fp.final_step else '(none)'}")
    for s in fp.steps:
        mark = "  final" if s.final else ""
        opt = " (optional)" if s.optional else ""
        print(f"  {s.id:22s} {s.action:7s} selectors={len(s.selectors)}{opt}{mark}")
    if not fp.verified:
        print("\n⚠️ NOT verified — selectors have not been confirmed against G0 recon.")
        print("   Live purchasing is guardrail-blocked until recon selectors are filled in")
        print("   and `verified: true` is set. Drills may still run for validation.")
    return 0


def cmd_learn_windows(args: argparse.Namespace) -> int:
    from .dropwindows import learn_hot_hours, observed_first_seen_hours

    db_path = Path(args.state_dir) / "state.sqlite"
    hours = observed_first_seen_hours(db_path)
    windows = learn_hot_hours(hours)
    print(f"observed {len(hours)} sighting timestamps")
    print(f"learned hot windows (local hours): {windows}")
    print("apply by setting watch.hot_hours_local in targets.yaml to the above.")
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

    sp = sub.add_parser("report", help="availability: has the target shown up, how often")
    sp.add_argument("--chip", default="M3 Ultra", help="chip substring filter (default: 'M3 Ultra')")
    sp.set_defaults(fn=cmd_report)

    def add_buyer_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--targets", default=str(DEFAULT_TARGETS))
        sp.add_argument("--flightplan", default=str(DEFAULT_FLIGHTPLAN))
        sp.add_argument("--profile-dir", default=str(DEFAULT_PROFILE))
        sp.add_argument("--browser-path", default=None)
        sp.add_argument("--headed", action="store_true", help="show the browser window")

    sp = sub.add_parser("watch", help="run the detection loop")
    add_buyer_args(sp)
    sp.set_defaults(fn=cmd_watch)

    sp = sub.add_parser("drill", help="gates 2.1/2.2: rehearse the strike path")
    add_buyer_args(sp)
    sp.add_argument("--url", default=None, help="drill target product URL")
    sp.set_defaults(fn=cmd_drill)

    sp = sub.add_parser("session-check", help="gate 2.3/4.2: Apple ID session validity")
    add_buyer_args(sp)
    sp.set_defaults(fn=cmd_session_check)

    sp = sub.add_parser("heartbeat", help="gate 4.1: watcher liveness")
    add_buyer_args(sp)
    sp.set_defaults(fn=cmd_heartbeat)

    sp = sub.add_parser("race-ready", help="compute + log the operational SLO snapshot")
    add_buyer_args(sp)
    sp.set_defaults(fn=cmd_race_ready)

    sp = sub.add_parser("flightplan", help="validate flightplan.yaml + show status")
    sp.add_argument("--flightplan", default=str(DEFAULT_FLIGHTPLAN))
    sp.set_defaults(fn=cmd_flightplan)

    sp = sub.add_parser("learn-windows", help="gate 4.4: recompute hot hours from history")
    sp.set_defaults(fn=cmd_learn_windows)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
