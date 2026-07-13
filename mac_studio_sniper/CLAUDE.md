# CLAUDE.md — mac_studio_sniper

Guidance for AI assistants (and humans) working on this subproject. Read
this before changing anything under `mac_studio_sniper/`.

## What this is

A standalone always-on app that detects and (optionally) auto-purchases a
refurbished **Mac Studio M3 Ultra** (512 GB priority, 256 GB acceptable)
from Apple's US refurbished store within its minutes-long availability
window. It is intentionally **not** part of the Hermes agent core or the
distributed wheel — it lives in the repo for convenience and reuses a few
Hermes facilities (Camoufox browser, cron, messaging gateway) when run
inside Hermes, but it stands alone with plain `python -m mac_studio_sniper`.

Authoritative design + goal docs:
- Architecture: `../.plans/mac-studio-refurb-sniper.md`
- Success gates (pass/fail, numeric): `../.plans/mac-studio-refurb-sniper-goal.md`
- Operator runbook: `RUNBOOK.md`
- Operator recon steps: `RECON.md`
- User-facing overview: `README.md`

## The one idea that governs everything: no LLM in the hot path

The race is won or lost in seconds. The detect→buy sequence is 100%
deterministic, pre-compiled Python. LLMs sit *around* it (recon authoring,
self-heal, talking to the human), never *in* it. If you are tempted to put
a model call inside `watcher.py` or `buyer.py`'s step loop, stop — that is
the one architectural line this project does not cross.

## Module map

| File | Role | LLM? |
|---|---|---|
| `watcher.py` | async poll loop: fetch → parse → dedup → match → alert → on_match hook | never |
| `parser.py` | schema-tolerant tile extraction from HTML/HAR | never |
| `matcher.py` | targets.yaml → MatchResult; needs_verification flag | never |
| `transport.py` | curl_cffi impersonation + httpx fallback + Akamai block detection | never |
| `notify.py` | Telegram + Twilio + console alerts | never |
| `state.py` | SQLite: sightings, alerts, polls, drills, purchases, checks, race_ready | never |
| `flightplan.py` / `flightplan.yaml` | externalized checkout script (selectors/URLs/assertions) | never |
| `buyer.py` | Playwright strike executor: drill / confirm / full-auto | never |
| `guardrails.py` | code-enforced arm checks, re-verified at strike time | never |
| `secrets.py` | CVV from env/file, 600-perm enforced | never |
| `interact.py` | human broker: confirm-to-buy + 2FA relay | never |
| `supervisor.py` | heartbeat, session check, drill driver, race-ready SLO | never |
| `heal.py` | bundles failure artifacts, drill-verifies a proposed flightplan | **agent seam** |
| `dropwindows.py` | learns hot polling hours from sighting history | never |
| `recon.py` | Playwright grid capture + checkout selector recorder + telegram setup | never |
| `cli.py` | one subcommand per success gate | never |

`heal.py::attempt_heal(heal_fn=...)` is the **only** place a model is
invoked, and even there the model's output is never trusted directly — it
must make a real drill pass before it is promoted.

## Invariants — do not break these

1. **`targets.yaml` is the only file that authorizes spending.** It is
   human-edited only. The supervisor / self-heal may patch `flightplan.yaml`
   but must NEVER write `targets.yaml`. Enforced by convention + review;
   if you add auto-editing, scope it to flightplan.yaml explicitly.
2. **Guardrails are enforced in code at strike time**, inside
   `buyer.py::attempt_purchase` and again right before the final click —
   not just in the caller. No prompt, config comment, or web page content
   can raise a price cap or bypass the SKU allowlist. `guardrails.check_arm`
   returning `[]` is the only thing that permits a live purchase.
3. **`flightplan.verified` gates live buying.** It ships `false` with
   placeholder selectors. Anything that flips it to `true` must be preceded
   by a passing drill. Do not default it to true, do not skip it in tests
   that assert the buy path is blocked.
4. **A drill never executes the final step.** `buyer._run` returns success
   for a drill the moment it reaches the `final` step. If you refactor the
   step loop, preserve this — a drill that places a real order is a sev-1.
5. **One success then disarm.** `stop_after_first_success` + the purchases
   table prevent a second buy. Keep the check in `guardrails`.
6. **Secrets never touch disk state or logs.** CVV flows env/file → in-memory
   → `fill` step only. Never log it, never write it to `state.sqlite` or an
   artifact.
7. **The kill switch (`<state_dir>/KILL`) halts within one step.** Both the
   watcher loop and the buyer step loop check it. Preserve both checks.

## The G0 seam (why placeholders exist)

apple.com blocks datacenter IPs and the checkout DOM can only be captured
from an authenticated residential session. So the real selectors cannot be
known at build time. The system is built complete with placeholder
selectors and `verified: false`; the operator runs `recon-checkout`, the
selectors land in `flightplan.yaml`, a drill verifies, and the flag flips.
When editing selectors, edit `flightplan.yaml` — never hard-code selectors
in `buyer.py`.

## Testing conventions

- Run: `python -m pytest tests/mac_studio_sniper/ -q`
- Browser tests (`test_buyer_browser.py`, `test_heal_browser.py`) drive a
  real Chromium against an **in-process fake Apple shop**
  (`tests/mac_studio_sniper/fake_shop.py`) — no network, no apple.com. They
  auto-skip when no Chromium binary is present (they look for
  `/opt/pw-browsers/chromium`; set `SNIPER_USE_SYSTEM_CHROMIUM=1` to force).
- When you change the buyer's step engine or guardrails, add/extend a
  fake-shop test — do not rely on unit tests alone for the buy path.
- `test_flightplan.py::test_repo_flightplan_is_valid_and_unverified` guards
  invariant #3: the shipped flightplan must parse and stay `verified:false`.
- Lint: `python -m ruff check mac_studio_sniper tests/mac_studio_sniper`
  (repo enforces PLW1514 — always pass `encoding="utf-8"` to file I/O).

## Where the Claude Agent SDK plugs in

Two seams, both outside the hot path:
- **Self-heal**: pass an async `heal_fn(bundle_dir) -> patched_yaml | None`
  to `heal.attempt_heal`. The bundle has `brief.json`, `dom.html`, and a
  failure screenshot. Promotion is always drill-gated.
- **Supervisor scheduling**: the `drill / heartbeat / session-check /
  race-ready` CLI commands are meant to run on timers (systemd or Hermes
  cron). They are pure functions over the state DB, so an agent can invoke
  them and read `status` / `race-ready` output to reason about health.

## Ethical / legal note (keep this honest in any user-facing text)

Automated purchasing violates Apple's site terms; orders can be canceled
and accounts flagged. The design deliberately stays at human-plausible
request rates, one account, one unit, with `confirm` mode keeping a human
in the loop. Do not add multi-account, proxy-rotation-for-evasion, or
inventory-hoarding features — they are out of scope by design, not by
omission.
