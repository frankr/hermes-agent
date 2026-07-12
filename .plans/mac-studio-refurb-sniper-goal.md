# Goal: Mac Studio Refurb Sniper — Implementation Gates

Companion to `.plans/mac-studio-refurb-sniper.md` (architecture). This file
defines the goal and the **measurable gates** that must pass, in order, for
the system to be considered race-ready and then successful. Each gate is
pass/fail with numbers — no gate is "done" by code review alone.

## Ultimate goal

> **Place a confirmed Apple order for one refurbished Mac Studio M3 Ultra —
> 512 GB RAM (priority 1) or 256 GB RAM (priority 2) — at or below the
> price caps in `targets.yaml`.**

Success evidence: Apple order confirmation email + order number logged in
`state.sqlite`.

Because Apple controls when stock appears, the ultimate goal has a
**conditional success criterion** we fully control:

> On any live drop of a matching SKU, the system detects it within 60 s of
> first availability and reaches "Place Order" (or the confirm prompt)
> within 90 s of detection. If the unit still sells out first, that drop
> counts as a *system pass* — losing to faster buyers is a market outcome;
> detecting late or breaking mid-checkout is a system failure and triggers
> a postmortem issue.

## Operational SLO (holds from Gate 2 onward)

**Race-ready ≥ 95% of wall-clock hours**, where race-ready :=
watcher process up AND last poll succeeded < 2 min ago AND Apple ID
session valid AND last full drill passed < 48 h ago. The supervisor
computes and logs this hourly; any drop below 95% over a rolling 7 days
pages the user.

---

## Gate 0 — Recon complete (target: ~2 days)

Manual capture from a residential connection; outputs checked into the repo.

| # | Criterion | Measure |
|---|---|---|
| 0.1 | Grid-page HAR captured and tile JSON located | Parser fixture test extracts **100%** of Mac Studio tiles from the HAR (part number, price, chip, RAM, URL) with **0 parse errors** |
| 0.2 | Full checkout HAR captured on a cheap in-stock refurb item | Every checkout step enumerated in `flightplan.yaml` v1 with primary selector + ≥1 fallback + expected-page assertion |
| 0.3 | Price caps grounded in current data | `targets.yaml` caps set from ≥3 observed/recorded M3 Ultra refurb listings (or new-price −12% floor if none observable) |
| 0.4 | Transport viability proven | `curl_cffi` Chrome-impersonated GET of the grid returns HTTP 200 with parseable tile JSON from the target deployment machine, 20/20 attempts over 1 h |

## Gate 1 — Detection & alerting live (target: ~1 week)

The watcher alone already delivers value: a human with a fast alert can buy manually.

| # | Criterion | Measure |
|---|---|---|
| 1.1 | Continuous operation | 72 h unattended run, poll success rate ≥ **99%**, zero unhandled exceptions |
| 1.2 | No bot-defense escalation | **0** Akamai block/challenge events (403/JS challenge) across the 72 h at production cadence |
| 1.3 | Detection latency | Synthetic-drop fixture (injected tile) alerts Telegram + SMS in **< 30 s p95** from injection |
| 1.4 | Alert hygiene | Zero duplicate alerts for the same part number within 24 h; every real grid change during the soak window detected (audited against a 60 s-interval reference log) |
| 1.5 | Real-world validation | ≥ 1 real refurb inventory change (any Mac model) detected and alerted during the soak |

## Gate 2 — Strike path rehearsed (target: ~2 weeks)

No live purchases; drills stop at the final button.

| # | Criterion | Measure |
|---|---|---|
| 2.1 | Drill reliability | **7 consecutive** daily dry-run drills pass end-to-end (product page → Place-Order screen) on live in-stock refurb items |
| 2.2 | Strike latency | Drill end-to-end time **p95 < 60 s**, measured per step and logged to `state.sqlite` |
| 2.3 | Session persistence | Apple ID session survives **7 days** with no manual re-login, verified by daily authenticated account-page load |
| 2.4 | 2FA broker exercised | One forced re-login completed end-to-end via Telegram code relay in < 5 min |
| 2.5 | Failure telemetry | An intentionally broken selector produces screenshot + DOM dump + user notification in < 60 s |

## Gate 3 — Armed (confirm mode) (target: ~3 weeks)

| # | Criterion | Measure |
|---|---|---|
| 3.1 | Guardrail test suite green in CI | Automated tests prove: over-cap price **blocked**, non-allowlisted SKU **blocked**, quantity ≠ 1 **blocked**, kill-switch halts in-flight run < 1 s, post-success disarm verified |
| 3.2 | Confirm loop timed | Synthetic match → confirm push delivered < 30 s; user "BUY" reply resumes and reaches final click < 15 s |
| 3.3 | Staleness interlock | Buyer refuses to arm when last drill is > 48 h old or failing (test-verified) |
| 3.4 | Live rehearsal | One real sub-$500 refurb accessory/Mac purchased end-to-end in confirm mode (returnable within Apple's window) proving the *full* path including Place Order |

Full-auto mode may be enabled only after every 3.x line passes **and** the
user explicitly flips `mode: full-auto`.

## Gate 4 — Self-sustaining supervision (target: ~4 weeks, then ongoing)

| # | Criterion | Measure |
|---|---|---|
| 4.1 | Watchdog | Induced watcher crash → restart or page in < **15 min** |
| 4.2 | Session-decay detection | Induced cookie expiry detected and escalated in < **12 h** |
| 4.3 | Self-heal | Induced selector rename → supervisor patches `flightplan.yaml`, drill re-verifies, patch committed to git, **< 24 h**, zero human code edits |
| 4.4 | Drop-window model | ≥ 2 weeks of observed refurb-change timestamps collected; watcher cadence provably tightens (logged interval change) inside learned windows |
| 4.5 | SLO attainment | Race-ready SLO (≥ 95%) held over a rolling 14-day window |

## Exit / abort criteria

- **Success exit:** confirmed order at/below cap → system disarms itself,
  final report generated, watcher optionally kept in alert-only mode.
- **Abort triggers (page user, disarm buyer, keep alerts):** Apple account
  flagged/order-canceled events ≥ 1; sustained Akamai blocking (> 25% of
  polls over 24 h) that cadence reduction doesn't fix; any purchase
  executed outside `targets.yaml` bounds (treated as a sev-1 defect —
  should be impossible per gate 3.1).
