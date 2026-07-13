# Mac Studio Refurb Sniper

Detects and (optionally) auto-purchases a refurbished **Mac Studio M3 Ultra**
(512 GB priority, 256 GB acceptable) from Apple's US refurb store within the
minutes-long availability window.

- Architecture: [`.plans/mac-studio-refurb-sniper.md`](../.plans/mac-studio-refurb-sniper.md)
- Success gates: [`.plans/mac-studio-refurb-sniper-goal.md`](../.plans/mac-studio-refurb-sniper-goal.md)
- G0 operator recon: [`RECON.md`](RECON.md)

## What's implemented

| Layer | Module | Status |
|---|---|---|
| Detection hot loop | `watcher.py` | ✅ jittered poll, hot windows, ETag, kill switch, inject channel |
| Tile parsing | `parser.py` | ✅ schema-tolerant HTML/HAR extraction |
| Matching | `matcher.py` | ✅ targets.yaml, price caps, needs-verification flag |
| Transport | `transport.py` | ✅ curl_cffi impersonation + httpx fallback + block detection |
| Alerts | `notify.py` | ✅ Telegram + Twilio SMS + console |
| State | `state.py` | ✅ SQLite: sightings, alerts, polls, drills, purchases, checks, SLO |
| Recon automation | `recon.py` | ✅ grid capture, checkout selector recorder, telegram setup |
| Strike path | `buyer.py` | ✅ flightplan executor, drill/confirm/full-auto, 2FA broker, artifacts |
| Flightplan | `flightplan.py` + `flightplan.yaml` | ⚠️ engine done; **selectors are placeholders until G0** |
| Guardrails | `guardrails.py` | ✅ code-enforced arm checks (gate 3.1) |
| Human broker | `interact.py` | ✅ Telegram + file confirm/2FA relay |
| Supervisor | `supervisor.py` | ✅ heartbeat, session check, drill, race-ready SLO |
| Self-heal | `heal.py` | ✅ bundle + drill-verified promotion (agent seam pluggable) |
| Drop-window learning | `dropwindows.py` | ✅ learns hot hours from sighting history |

**The one thing blocking live purchase is G0 recon.** The flightplan ships
`verified: false` with placeholder selectors; guardrails refuse to arm
until real selectors are filled in and the flag is flipped. Everything
else is built and tested (71 tests, including real-browser drill/confirm/
heal runs against an in-process fake shop).

## The G0 seam — filling in missing recon data

After running `recon-checkout` (see RECON.md), you get a
`checkout-selectors-*.txt`. To wire it in:

1. Replace each `TODO-RECON` selector in `flightplan.yaml` with the
   `data-autom`/id selectors from the recon report.
2. Set `drill_grid_url` to a page listing a cheap in-stock refurb item.
3. Fill `session_check` and `signin_detect_selectors` from the report.
4. Run a drill: `python -m mac_studio_sniper drill` — it walks everything
   up to (not through) Place Order against a real cheap item.
5. When the drill passes, set `verified: true`. Guardrails now allow
   arming (still gated on mode, price, drill freshness, etc.).

Price caps go in `targets.yaml` (never auto-edited) — use the numbers
`recon-grid` suggests.

## Running it

```bash
pip install httpx pyyaml curl_cffi playwright && playwright install chromium

# alert-only (default, no purchasing) — start the G1 soak
python -m mac_studio_sniper watch

# once flightplan is verified + targets.yaml mode: confirm
python -m mac_studio_sniper watch          # buyer arms; confirms via Telegram

# supervisor commands (schedule these — see below)
python -m mac_studio_sniper drill          # daily rehearsal
python -m mac_studio_sniper session-check  # is the Apple ID session alive
python -m mac_studio_sniper heartbeat      # is the watcher polling
python -m mac_studio_sniper race-ready     # SLO snapshot
python -m mac_studio_sniper status         # gate metrics
```

### Scheduling (systemd example)

`watch` runs as a long-lived service; the supervisor commands run on
timers. Minimal unit + timers:

```ini
# ~/.config/systemd/user/sniper-watch.service
[Service]
ExecStart=%h/hermes-agent/.venv/bin/python -m mac_studio_sniper watch
Restart=always
RestartSec=5
Environment=SNIPER_TELEGRAM_BOT_TOKEN=...
Environment=SNIPER_TELEGRAM_CHAT_ID=...

# sniper-drill.timer → OnCalendar=*-*-* 03:00:00   (daily drill)
# sniper-heartbeat.timer → OnCalendar=*:0/10       (every 10 min)
# sniper-session.timer → OnCalendar=*-*-* */6:00:00 (every 6h)
# sniper-raceready.timer → OnCalendar=*:0/60        (hourly SLO log)
```

`Restart=always` is the gate-4.1 watchdog. `heartbeat` is the backstop:
if the watcher is wedged but not crashed, a stale poll pages you.

### Hermes cron alternative

If running inside Hermes, register the four supervisor commands as cron
jobs via `cron/scheduler.py` and route alerts through the messaging
gateway instead of Telegram directly.

## Self-heal with a Claude agent

`heal.attempt_heal(..., heal_fn=...)` accepts any async function that
takes a bundle directory (containing `brief.json`, `dom.html`, a failure
screenshot) and returns patched flightplan YAML. Plug in:

- **Claude Agent SDK**: an agent that reads the bundle, inspects the DOM,
  and returns corrected selectors.
- **Hermes subagent**: `tools/delegate_tool.py` spawning an isolated agent.
- **Human**: edit the candidate by hand.

The promotion is always drill-verified: a proposed patch that doesn't make
a real drill pass is rejected and the live flightplan is left untouched.

## Safety model

- `targets.yaml` is the only file that authorizes spending; never
  auto-edited (the supervisor may only patch `flightplan.yaml`).
- Guardrails (`guardrails.py`) are re-checked at strike time inside the
  buyer, not just by the caller: unverified flightplan, over-cap price,
  wrong SKU, quantity ≠ 1, stale/absent drill, kill switch, and
  post-success disarm all hard-block.
- `confirm` mode keeps a human in the loop (reply BUY within the timeout);
  `full-auto` requires every guardrail green and an explicit config flip.
- CVV is read from env/`age`/keyring at runtime, never stored or logged.
- `touch ~/.mac_studio_sniper/KILL` halts the watcher and any in-flight
  purchase within one step.
