# Mac Studio Refurb Sniper — Operator Runbook

The durable, start-to-finish operating guide. If you only read one file,
read this. Companion docs: `README.md` (overview), `RECON.md` (G0 detail),
`CLAUDE.md` (for AI assistants), `../.plans/*` (design + gates).

Everything runs from an **always-on machine on a residential connection**.
apple.com blocks datacenter IPs — a cloud VM will get challenged.

---

## 0. First-time setup (once, ~5 min)

```bash
git clone <hermes-agent repo> && cd hermes-agent
git checkout claude/mac-studio-purchase-bot-w04mkm
python3 -m venv .venv && source .venv/bin/activate
pip install httpx pyyaml curl_cffi playwright
playwright install chromium
```

Sanity check the code is healthy:

```bash
python -m mac_studio_sniper flightplan     # should print verified: False
python -m pytest tests/mac_studio_sniper/ -q   # optional; 71 tests
```

---

## 1. Bring-up sequence (the happy path, in order)

Each step maps to a success gate in `../.plans/mac-studio-refurb-sniper-goal.md`.

### Step 1 — Grid capture · gates 0.1 + 0.3 (~2 min)

```bash
python -m mac_studio_sniper recon-grid
```

- Prints the tiles found + suggested price caps.
- **Verify the tile count matches the live page** in a normal browser.
- If tiles are missing → the parser needs a patch; keep
  `~/.mac_studio_sniper/recon/grid-*.html` and hand it to your AI assistant.
- Copy the suggested caps into `targets.yaml` (`max_price_usd`).

### Step 2 — Checkout walk · gate 0.2 (~10 min, the one manual part)

```bash
python -m mac_studio_sniper recon-checkout
```

A visible browser opens with a persistent profile:
1. Sign in to your Apple ID (2FA happens here, once).
2. Pick the **cheapest in-stock refurb item** (any category).
3. Walk: Add to Bag → Check Out → shipping → payment.
4. **Stop on the Place Order page — do NOT click Place Order.**
5. Close the window.

Output: `checkout-selectors-<ts>.txt` (safe to share; input values are
never recorded) and a `checkout-<ts>.har` (contains cookies — keep local).

### Step 3 — Wire the flightplan · unblocks live buying

Edit `mac_studio_sniper/flightplan.yaml`:
- Replace every `TODO-RECON` selector with the `data-autom`/id selectors
  from the recon report.
- Set `drill_grid_url` to a page listing a cheap in-stock refurb item.
- Fill `session_check` and `signin_detect_selectors`.
- Leave `verified: false` for now.

### Step 4 — Drill · gates 2.1 + 2.2

```bash
python -m mac_studio_sniper drill
```

Walks every step up to (not through) Place Order against a real cheap item.
On pass, per-step latency prints. When a drill passes cleanly:
- set `verified: true` in `flightplan.yaml`.
- Repeat daily; gate 2.1 wants **7 consecutive** passing drills.

### Step 5 — Alerts · gate 1.3

```bash
# @BotFather → /newbot → copy token → message the bot once, then:
python -m mac_studio_sniper telegram-setup --token <TOKEN>
export SNIPER_TELEGRAM_BOT_TOKEN=<TOKEN>
export SNIPER_TELEGRAM_CHAT_ID=<printed id>
```

### Step 6 — Transport viability · gate 0.4

```bash
python -m mac_studio_sniper probe          # ~1 h; want 20/20 clean, 0 blocks
```

Confirm it prints `curl_cffi`. Blocks here mean escalate to browser-based
polling (a follow-up ticket).

### Step 7 — Go live

```bash
# alert-only first (no purchasing) — starts the 72h soak (gate 1.1)
python -m mac_studio_sniper watch

# verify the alert path end-to-end (in another shell):
python -m mac_studio_sniper inject         # phone should buzz < 30 s
```

Then arm, when drills are green and you trust it:

```bash
# edit targets.yaml → mode: confirm
python -m mac_studio_sniper watch          # asks "BUY?" on a real hit
# later, after a confirmed live buy, optionally → mode: full-auto
```

---

## 2. Steady-state operation

Run `watch` as a long-lived service and the supervisor commands on timers.

### systemd (user units)

```ini
# ~/.config/systemd/user/sniper-watch.service
[Service]
WorkingDirectory=%h/hermes-agent
ExecStart=%h/hermes-agent/.venv/bin/python -m mac_studio_sniper watch
Restart=always
RestartSec=5
Environment=SNIPER_TELEGRAM_BOT_TOKEN=...
Environment=SNIPER_TELEGRAM_CHAT_ID=...
Environment=SNIPER_CVV=...            # only if arming; prefer a credential store
[Install]
WantedBy=default.target
```

Timers (create matching `.timer` + `.service` pairs):

| Timer | Cadence | Command | Gate |
|---|---|---|---|
| sniper-heartbeat | every 10 min | `heartbeat` | 4.1 |
| sniper-session | every 6 h | `session-check` | 2.3 / 4.2 |
| sniper-drill | daily 03:00 | `drill` | 2.1 / 2.2 |
| sniper-raceready | hourly | `race-ready` | SLO |
| sniper-learnwindows | weekly | `learn-windows` | 4.4 |

`Restart=always` is the gate-4.1 watchdog. `enable --now` each unit.

### Hermes cron alternative

Inside Hermes, register those five commands via `cron/scheduler.py` and
route alerts through the messaging gateway instead of Telegram directly.

---

## 3. Health & status

```bash
python -m mac_studio_sniper status         # sightings, poll rate, blocks, drill streak
python -m mac_studio_sniper race-ready      # is the system race-ready right now + 7d SLO
```

**Race-ready** means: watcher polling (< 2 min since last good poll) AND
Apple ID session valid AND last drill passed < 48 h ago AND flightplan
verified. The SLO target is ≥ 95% of hours. If `race-ready` is red, its
output names exactly which condition failed.

---

## 4. Incident playbook

| Symptom | Likely cause | Action |
|---|---|---|
| `heartbeat: STALE` / no alerts | watcher down or wedged | `systemctl --user restart sniper-watch`; check `status` poll rate |
| Telegram says session check FAILED | Apple ID cookie expired | re-run `recon-checkout` (just sign in, close) to refresh the profile |
| Drill fails at a step | Apple changed the page | inspect `~/.mac_studio_sniper/artifacts/<ts>/`; patch `flightplan.yaml`; re-drill. Or run the self-heal loop (see CLAUDE.md) |
| `probe` shows blocks / 403s | IP challenged | back off cadence in `targets.yaml`; confirm residential IP; consider browser-poll fallback ticket |
| Match found but "NOT buying — guardrails" | a guardrail tripped | the alert lists which one; usually price over cap, unverified flightplan, or stale drill |
| 2FA prompt during a live run | session bounced mid-flow | reply to the Telegram prompt with the 6-digit code within 5 min |
| Ordered the wrong thing / want to stop NOW | — | `touch ~/.mac_studio_sniper/KILL` — halts watcher + any in-flight buy within one step |

### Emergency stop

```bash
touch ~/.mac_studio_sniper/KILL            # instant halt
# remove it to resume:
rm ~/.mac_studio_sniper/KILL
```

---

## 5. Files & locations

| Path | What |
|---|---|
| `mac_studio_sniper/targets.yaml` | **spending authority** — specs, price caps, mode. Human-edited only |
| `mac_studio_sniper/flightplan.yaml` | checkout selectors/steps. Supervisor-patchable |
| `~/.mac_studio_sniper/state.sqlite` | all durable state (sightings, drills, purchases, SLO) |
| `~/.mac_studio_sniper/browser-profile/` | the logged-in Apple ID browser context |
| `~/.mac_studio_sniper/artifacts/<ts>/` | failure screenshots + DOM dumps |
| `~/.mac_studio_sniper/recon/` | recon captures (HARs here contain PII — keep local) |
| `~/.mac_studio_sniper/KILL` | presence = emergency stop |
| `~/.mac_studio_sniper/cvv` | optional CVV file (must be chmod 600) |

Environment variables: `SNIPER_TELEGRAM_BOT_TOKEN`, `SNIPER_TELEGRAM_CHAT_ID`,
`SNIPER_TWILIO_*` (SMS escalation), `SNIPER_CVV` (arming).

---

## 6. Honest caveats

- Automated purchasing violates Apple's terms; orders can be canceled and
  accounts flagged. This runs at human-plausible rates, one account, one
  unit, `confirm`-first. Accept the residual risk before arming.
- You can still lose a race to faster infrastructure. The counters are poll
  cadence in learned windows, a < 60 s strike path, and the phone alert
  that lets you buy manually.
- 512 GB refurbs are rare; the 256 GB target raises your odds of any hit.
