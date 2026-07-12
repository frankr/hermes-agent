# Mac Studio Refurb Sniper — High-Level Design

## Objective

Automatically detect when Apple posts a refurbished **Mac Studio M3 Ultra
(512 GB RAM preferred, 256 GB acceptable)** to the US refurbished store
(https://www.apple.com/shop/refurbished/mac/mac-studio) and complete the
purchase before the unit sells out — a window that has historically been
**minutes or less**. One unit, one account, personal purchase. This is a
speed problem, not a scale problem: the design deliberately avoids
scalper-style techniques (no multi-account farming, no inventory hoarding,
no aggressive request flooding) both because they are unnecessary for a
single purchase and because they get accounts and IPs banned.

## Design Principles

1. **No LLM in the hot path.** The detect→purchase sequence must complete
   in seconds. Every step that runs when stock appears is deterministic,
   pre-compiled code (HTTP polling + a Playwright script). Claude agents
   sit *around* the hot path: they keep it healthy, repair it when Apple
   changes the site, and talk to the human.
2. **Rehearse constantly, strike rarely.** The purchase script is exercised
   end-to-end (up to, but not through, the final "Place Order" click)
   against cheap in-stock refurb items on a schedule. A strike path that
   hasn't run in two weeks is a strike path that fails.
3. **State lives outside any context window.** Targets, selectors, session
   cookies, seen-SKU history, and run logs are files/SQLite on disk. Any
   supervisor agent session can die and a new one resumes from disk — the
   standard Anthropic pattern for long-running agents (checkpoint to
   durable storage, compact aggressively, verify before promoting changes).
4. **Hard guardrails in code, not prompts.** Price caps, SKU allowlist,
   quantity=1, and one-success-then-disarm are enforced by the Python hot
   path. No agent, prompt, or webpage content can raise a price cap.
5. **Graceful degradation.** Every tier failing still leaves the user with
   a loud phone notification and a deep link — a human with a warm,
   logged-in browser tab is the fallback purchase mechanism.

## Verified constraints (recon so far)

- **Akamai Bot Manager fronts apple.com.** Plain `curl`/generic fetchers
  receive HTTP 403 (verified 2026-07-12). Polling must present a real
  browser TLS + header fingerprint: `curl_cffi` (Chrome impersonation) for
  the cheap path, or `fetch()` evaluated inside a warmed real-browser page
  (Camoufox/Chromium) which carries a genuine fingerprint *and* cookies.
- **The refurb grid page embeds structured JSON** (tile data: product
  title, part number `G…`, price, product URL) in a script tag, so
  detection is JSON parsing, not DOM scraping. Exact JSON path and the
  add-to-bag/checkout request shapes must be captured in **Phase 0 recon**
  (HAR capture from a real browser on a residential connection — this
  cannot be done from a datacenter sandbox).
- **Apple's bag does not reserve inventory.** Stock is claimed at "Place
  Order", not at "Add to Bag". Latency budget is end-to-end.
- **Refurb restocks cluster** in early-morning US hours (roughly 04:00–
  07:00 PT) and mid-week, but not reliably. Poll cadence adapts: tight in
  hot windows, relaxed overnight; the supervisor learns observed drop
  times into the state DB over time.

## Architecture

```
                        ┌────────────────────────────────────────────┐
                        │  SUPERVISOR (Claude Agent SDK, long-lived) │
                        │  heartbeats · dry-run drills · self-heal   │
                        │  2FA broker · drop-window learning         │
                        └───────▲───────────────────────┬────────────┘
                                │ reads logs/state       │ patches flightplan.yaml,
                                │ screenshots on failure │ re-runs drill, promotes
        ────────────────────────┼───────────────────────┼──────────────────
        HOT PATH (deterministic │ Python, no LLM)        │
                                │                        ▼
   ┌──────────────┐   match   ┌─┴──────────────────────────────┐
   │   WATCHER    │──────────►│            BUYER               │
   │ asyncio loop │           │ pre-warmed Playwright context  │
   │ 10–45 s poll │           │ signed-in Apple ID, saved      │
   │ curl_cffi +  │           │ address/payment · flightplan-  │
   │ browser-page │           │ driven selectors · guardrails  │
   │ fetch fallback│          └───────────┬────────────────────┘
   └──────┬───────┘                       │ confirm-mode hold /
          │ every hit, always             │ full-auto Place Order
          ▼                               ▼
   ┌─────────────────────────────────────────────────┐
   │  NOTIFIER — Telegram/Discord push (Hermes       │
   │  gateway) + Twilio SMS/voice call escalation.   │
   │  Carries deep link so a human can race manually. │
   └─────────────────────────────────────────────────┘

   Durable state (SQLite + YAML, the "memory" of the system):
   targets.yaml · flightplan.yaml · state.sqlite (seen SKUs, drops,
   drill results, session health) · encrypted secrets (keyring/age)
```

### 1. Watcher — detection hot loop (plain Python, asyncio)

- Polls the Mac Studio refurb grid (and the M3 Ultra filter URL, plus
  direct product URLs of previously-seen matching SKUs, which sometimes
  reappear) every **10–45 s with jitter**, conditional GETs where honored.
- Transport ladder: `curl_cffi` Chrome-impersonated requests → on 403/
  challenge, fall back to `fetch()` inside the warmed browser page → on
  sustained block, optionally a cloud fallback (Firecrawl / Browserbase)
  as a *detection-only* alternate vantage point.
- Parses the embedded tile JSON; matches against `targets.yaml`
  (spec + price-cap table). Dedups via `state.sqlite` (part number +
  first-seen timestamp).
- On match: **synchronously invokes the Buyer in-process** (no queue hop)
  and fires the Notifier in parallel. Detection→buyer-start target: <1 s.
- Rate discipline is a feature: an IP ban loses every future race. Cadence
  tightens (to ~10 s) only inside learned drop windows.

### 2. Buyer — purchase strike path (deterministic Playwright)

- A **persistent, pre-warmed browser context** (Camoufox or Chromium with
  a stable profile dir): already signed in to the Apple ID, shipping
  address and payment method saved on the account, cookies fresh. Signing
  in and typing card numbers during the race is how you lose it.
- Executes a **flightplan**: product URL → Add to Bag → Checkout → saved
  shipping → saved payment (+ CVV, stored encrypted, injected at runtime)
  → Place Order. Selectors, URLs, and expected-page assertions live in
  `flightplan.yaml`, *not* in code, so the supervisor can patch them
  without a deploy. Each step has a primary selector, ordered fallbacks,
  and a per-step timeout; any deviation screenshots + dumps DOM to disk
  and escalates.
- **Two arming modes:**
  - `confirm` — runs everything through payment, stops at Place Order,
    pushes a Telegram message ("M3 Ultra 512GB, $X — reply BUY within
    120 s"); a reply completes the click. Safe default while trust builds.
  - `full-auto` — places the order, then immediately disarms.
- **Guardrails (code-enforced):** SKU allowlist from `targets.yaml`; hard
  price cap per config; quantity exactly 1; global kill-switch file;
  one-success-then-disarm; refusal to run if the flightplan's last drill
  result is stale (>48 h) or failing.
- Latency budget: detection <1 s, buyer end-to-end **<60 s** (measured in
  drills; optimize the slowest steps first).

### 3. Supervisor — the long-running Claude agent

Built on the **Claude Agent SDK** (or run as a Hermes agent with cron
triggers — see "Where it lives"). It is *not* resident in the hot path;
it wakes on schedule and on failure events:

- **Heartbeat (every 6–12 h):** watcher process alive and polling
  successfully; Apple ID session still authenticated (loads account page
  in the warm context); cookie age; disk state sane. Repairs what it can,
  pages the user for what it can't.
- **Dry-run drill (daily, and after any flightplan change):** run the
  Buyer against a cheap in-stock refurb item through every step up to the
  final click, record per-step latency and screenshots to `state.sqlite`.
  A failing drill immediately flags the system "not race-ready" and
  notifies.
- **Self-heal:** when a drill or real run fails on a selector/interstitial,
  the agent gets the failure screenshot + DOM dump, proposes a
  `flightplan.yaml` patch, re-runs the drill to verify, and only then
  promotes the patch (and commits it to git for history). This is where
  browser-use/browser-harness style agentic browsing earns its keep —
  exploring the changed page — while the verified output is still a
  deterministic flightplan.
- **2FA broker:** Apple ID re-auth challenges are relayed to the user over
  Telegram; the user replies with the 6-digit code; the agent completes
  login in the persistent context. (Trusted-browser cookies make this
  rare, but it must be a paved path, not an outage.)
- **Drop-window learning:** logs every observed inventory appearance;
  periodically adjusts the watcher's hot-window schedule from actual data.

Long-running-agent hygiene (per Anthropic guidance): the agent's working
memory is the disk state, not its context — each wake-up starts by reading
`state.sqlite` + recent logs; context is compacted/discarded freely;
discrete diagnosis jobs (e.g. "why did step 4 fail") run as subagents;
every self-modification is verified by a drill before promotion.

### 4. Notifier

- Every stock hit notifies immediately (Telegram/Discord via the Hermes
  gateway), independent of what the Buyer does — the human is the backup
  buyer. Include product, price, and direct product URL.
- Escalation for matched targets: Twilio SMS + voice call (a phone ringing
  at 5 am is the point).
- Purchase outcomes, drill failures, and session-expiry warnings also
  notify, at lower urgency.

## Tool selection matrix

| Tool | Role | Why / why not |
|---|---|---|
| `curl_cffi` (Chrome-impersonate) | Watcher primary transport | Millisecond-cheap, beats Akamai TLS fingerprinting for GETs; no JS. |
| Playwright + persistent Chromium/Camoufox profile | Buyer strike path; watcher fallback transport | Only way to hold a logged-in session and click checkout fast; Camoufox (already in Hermes) adds stealth. |
| browser-use / browser-harness | Supervisor self-heal only | Agentic browsing is seconds-to-minutes slow — wrong for the hot path, right for exploring a changed page to regenerate the flightplan. |
| Firecrawl / Browserbase | Optional detection fallback vantage | Managed proxies/rendering if the home IP gets challenged; adds latency + cost, so never primary. |
| Claude Agent SDK | Supervisor | Long-lived orchestration, subagents, tool use; wakes on cron + failure events. |
| Hermes cron + messaging gateway | Scheduling + notifications | Already built: `cron/scheduler.py`, Telegram/Discord/WhatsApp delivery. |
| Twilio | Escalation | Wake-the-human channel. |

## Deployment reality

Run the watcher+buyer on an **always-on machine behind a residential IP**
(home server, Mac mini, or a box the user controls) — datacenter IPs draw
Akamai challenges and this sandbox demonstrably cannot even reach
apple.com. Hermes's local/Docker backends cover this. The supervisor can
run on the same host. Everything is one `docker compose up` /
`systemd` unit; secrets via OS keyring or `age`-encrypted file unlocked at
service start.

## Configuration sketch

```yaml
# targets.yaml — the only file that authorizes spending money
stop_after_first_success: true
quantity: 1
targets:
  - name: "M3 Ultra 512GB (any storage ≥1TB)"
    priority: 1
    match: { chip: "M3 Ultra", ram_gb: 512 }
    max_price_usd: 8600        # ~refurb on $9,499+ list — verify in Phase 0
  - name: "M3 Ultra 256GB"
    priority: 2
    match: { chip: "M3 Ultra", ram_gb: 256 }
    max_price_usd: 6100
mode: confirm                   # confirm | full-auto
confirm_timeout_s: 120
```

## Implementation phases

**Phase 0 — Recon (manual, ~half a day, from the user's home machine):**
capture HARs of (a) the refurb grid page, (b) a full checkout on any cheap
in-stock refurb item. Extract: tile JSON path, part-number/price fields,
add-to-bag request, checkout step sequence + selectors → initial
`flightplan.yaml`. Verify current M3 Ultra refurb pricing to set caps.

**Phase 1 — Watcher + alerts (first real value):** polling, matching,
SQLite state, Telegram + Twilio alerts. Even with no auto-buy, a sub-30 s
phone alert with a deep link puts the user ahead of most buyers.

**Phase 2 — Buyer in drill mode:** persistent logged-in context, flightplan
executor, daily drills, latency measurement. No live purchases yet.

**Phase 3 — Armed:** `confirm` mode on real hits; after a successful
confirmed purchase flow (or high drill confidence), optionally `full-auto`.

**Phase 4 — Supervisor:** heartbeats, self-heal loop, 2FA broker,
drop-window learning. This is what makes it survive weeks of waiting.

## Where it lives in this repo

New app package `apps/mac_studio_sniper/`:

```
apps/mac_studio_sniper/
  watcher.py        # asyncio poll loop + matcher
  buyer.py          # flightplan executor (Playwright)
  flightplan.yaml   # selectors/URLs/assertions (supervisor-patchable)
  targets.yaml      # specs + price caps (human-edited only)
  supervisor/       # Claude Agent SDK app: heartbeat, drill, heal, 2FA
  notify.py         # hermes gateway + twilio
  state.py          # sqlite schema + accessors
  cli.py            # arm/disarm/status/drill/kill-switch
```

Reuses: `tools/browser_camofox.py` (stealth browser), `cron/scheduler.py`
(heartbeat + drill schedules), the messaging gateway (alerts, confirm
replies, 2FA relay).

## Risks & honest caveats

- **ToS / account risk:** automated purchasing violates Apple's site terms;
  Apple can cancel orders or flag the account. Mitigations: human-speed
  action counts (a handful of requests per minute), one account, one unit,
  `confirm` mode keeping a human in the loop. Accept residual risk.
- **You can still lose the race.** If scalper infrastructure claims the
  unit at Place-Order faster, no architecture fixes that; the counter is
  poll cadence inside learned windows + a <60 s strike path + the human
  fallback alert.
- **Checkout flow churn:** Apple ships storefront changes regularly; the
  daily drill + self-heal loop exists precisely so breakage is discovered
  on a drill, not on the one drop that matters.
- **512 GB refurbs are genuinely rare** (top-bin chip only). The 256 GB
  target materially raises the odds of a hit while waiting.
