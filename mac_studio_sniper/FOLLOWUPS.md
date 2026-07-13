# Follow-up Tickets — Mac Studio Refurb Sniper

Durable ticket backlog. This is the **source of truth** for remaining work.
Each ticket is written to be picked up cold by a future Claude session
(Opus/Sonnet) — it names the trigger, the files to touch, and acceptance
criteria tied to the success gates in
`../.plans/mac-studio-refurb-sniper-goal.md`.

Status legend: 🔴 blocked on operator data · 🟡 ready to build · 🟢 done

**Mirrored in Linear** — project *Mac Studio Refurb Sniper*
([link](https://linear.app/buildwithspark/project/mac-studio-refurb-sniper-9cb16f24b86b)),
team Buildwithspark. Ticket IDs: T1 = BUI-26, T2 = BUI-27, T3 = BUI-28,
T4 = BUI-29, T5 = BUI-30, T6 = BUI-31, T7 = BUI-32, T8 = BUI-33,
T9 = BUI-34, T10 = BUI-35, T11 = BUI-36, T12 = BUI-37. This file is
authoritative if the two drift.

Most tickets are **blocked on G0 recon** (the operator running
`recon-grid` / `recon-checkout` from a residential machine). Once that data
exists, the wiring tickets (T1–T3) unblock the rest and the system can go
live. See `RUNBOOK.md` §1 for the operator sequence.

---

## T1 — Wire real checkout selectors into flightplan.yaml 🔴
**Gate:** 0.2 → unblocks 2.x, 3.x
**Trigger:** operator has run `recon-checkout` and provided
`checkout-selectors-<ts>.txt` (and ideally the step URL list).
**Do:**
- Replace every `TODO-RECON` selector in `flightplan.yaml` with the
  `data-autom`/id selectors from the recon report; prefer `data-autom`.
- Set `drill_grid_url`, `session_check`, and `signin_detect_selectors`
  from the report.
- Confirm the step sequence matches the observed flow (add/remove steps
  only if the real flow structurally differs; preserve exactly one `final`
  step, last).
- Keep `verified: false` — T2 flips it after a passing drill.
**Accept:** `python -m mac_studio_sniper flightplan` shows the real
selectors; `Flightplan.load` passes validation; no `TODO-RECON` remains.
**Files:** `flightplan.yaml`.
**Notes:** never hard-code selectors in `buyer.py`. Watch for A/B variants
of Apple's checkout — capture both and add fallbacks in the `selectors`
list if the recon walk hit an unusual variant.

## T2 — First passing drill + flip verified 🔴
**Gate:** 2.1 (streak start), 2.2 (latency)
**Trigger:** T1 done, on the deployment machine with the logged-in profile.
**Do:**
- `python -m mac_studio_sniper drill` against a cheap in-stock refurb.
- Debug failures from `~/.mac_studio_sniper/artifacts/<ts>/` (screenshot +
  DOM). Iterate on `flightplan.yaml` selectors/timeouts.
- When it passes cleanly, set `verified: true`.
- Record baseline per-step latencies; if p95 > 60 s, open T7.
**Accept:** one clean drill; `verified: true`; `race-ready` no longer cites
the flightplan.
**Files:** `flightplan.yaml` (selectors/timeouts only).

## T3 — Ground price caps from live data 🔴
**Gate:** 0.3
**Trigger:** `recon-grid` output available (or a refurb price tracker).
**Do:** set `max_price_usd` for both targets in `targets.yaml` from
observed M3 Ultra listings (recon-grid prints suggestions). If none are
live, use list × 0.88 and refine on first real sighting.
**Accept:** caps reflect ≥ 3 observed/recorded listings, or the documented
fallback. `targets` dry-run shows expected matches on a captured grid.
**Files:** `targets.yaml` (human-edited only — never auto-patch this file).

## T4 — 72-hour detection soak 🔴
**Gate:** 1.1 (≥ 99% poll success), 1.2 (0 blocks), 1.4 (dedup), 1.5
**Trigger:** watcher deployable on the always-on residential box.
**Do:** run `watch` (alert-only) 72 h; monitor `status`. Investigate any
block events (adjust cadence/headers). Confirm ≥ 1 real inventory change
caught during the window.
**Accept:** the gate numbers in `status` over the window.
**Files:** possibly `transport.py` headers / `targets.yaml` cadence.

## T5 — Guardrail suite in CI 🟡
**Gate:** 3.1
**Trigger:** ready now (tests exist; CI wiring is the gap).
**Do:** ensure `tests/mac_studio_sniper/test_guardrails.py` +
`test_buyer_browser.py` run in the repo's CI. The browser tests need a
Chromium binary in CI (or keep them skip-guarded and run a scheduled
browser-enabled job). Add a CI lane that fails the build if the shipped
`flightplan.yaml` is ever committed with `verified: true` alongside
`TODO-RECON` markers.
**Accept:** green CI lane covering every guardrail path; the "shipped plan
stays unverified" invariant is enforced by a test.
**Files:** `.github/workflows/*`, maybe a small guard test.

## T6 — Live confirm-mode rehearsal purchase 🔴
**Gate:** 3.4
**Trigger:** T2 done, `mode: confirm`, drills green.
**Do:** buy one real sub-$500 returnable refurb accessory end-to-end in
confirm mode (proves the path *through* Place Order). Return it within
Apple's window. Verify `purchases` table + disarm behavior.
**Accept:** a real order number recorded; system auto-disarms
(`stop_after_first_success`).
**Risk:** spends real money; returnable item only.

## T7 — Strike-latency optimization (if needed) 🟡→🔴
**Gate:** 2.2 (p95 < 60 s)
**Trigger:** T2 shows p95 > 60 s.
**Do:** profile per-step timings (already logged); pre-warm the context,
trim `wait_until` strictness, parallelize independent waits, reduce
per-selector fallback timeouts on the hot steps. Consider keeping a warm
page pre-navigated to the product URL pattern.
**Accept:** drill p95 < 60 s sustained over 7 drills.
**Files:** `buyer.py`, `flightplan.yaml` timeouts.

## T8 — Self-heal agent wired to Claude Agent SDK 🟡
**Gate:** 4.3
**Trigger:** ready to build; valuable once drills run regularly.
**Do:** implement a concrete `heal_fn(bundle_dir) -> yaml|None` using the
Claude Agent SDK (or a Hermes subagent via `tools/delegate_tool.py`). It
reads `brief.json` + `dom.html` + screenshot and returns corrected
flightplan YAML. Add a supervisor hook that, on a drill failure, calls
`heal.attempt_heal` and notifies on the outcome. Keep promotion
drill-gated (already enforced in `heal.py`).
**Accept:** induced selector rename → agent patches `flightplan.yaml`,
drill re-verifies, patch git-committed, no human code edit, < 24 h.
**Files:** new `heal_agent.py`, hook in `supervisor.py`.
**Model note:** this ticket (and T1, T7, T9) is where an Opus/Sonnet
session does real work — the DOM-diffing/selector-repair reasoning is the
LLM-appropriate part of the system.

## T9 — Browser-based polling fallback 🔴
**Gate:** supports 1.2 if `probe` (0.4) shows blocks
**Trigger:** `curl_cffi` transport draws Akamai challenges from the deploy
IP.
**Do:** add a transport tier that runs `fetch()` of the grid JSON inside
the warmed Camoufox/Chromium context (genuine fingerprint + cookies).
Wire it as the fallback in `transport.fetch`'s ladder. Reuse
`tools/browser_camofox.py` when running under Hermes.
**Accept:** grid polls succeed via the browser tier when curl_cffi is
blocked; block-event rate returns under gate 1.2's bar.
**Files:** `transport.py`, new browser-poll helper.

## T10 — Supervisor as a resident loop / Hermes cron 🟡
**Gate:** 4.1, 4.5 (SLO attainment)
**Trigger:** ready now.
**Do:** ship the systemd unit + timer files (templated in `RUNBOOK.md` §2)
under a `deploy/` dir, or register the supervisor commands as Hermes cron
jobs. Ensure `race-ready` logs hourly so the 7/14-day SLO is measurable.
**Accept:** watchdog restarts a killed watcher < 15 min; SLO computed over
a rolling window.
**Files:** new `deploy/systemd/*`, or a `cron/` registration.

## T11 — Drop-window learning promoted into config 🟡
**Gate:** 4.4
**Trigger:** ≥ 2 weeks of sightings accumulated.
**Do:** have the supervisor periodically run `learn-windows` and write the
result into `targets.yaml`'s `watch.hot_hours_local` (this is the one
supervisor write to targets.yaml that is safe — it's cadence, not spending
authority; gate it behind a clearly-scoped function and log the change).
Alternatively keep it advisory (print + notify) and let the human apply.
**Accept:** observed logged interval tightening inside learned windows.
**Files:** `supervisor.py`, `dropwindows.py`.
**Caution:** re-read invariant #1 in `CLAUDE.md` — if automating the
write, touch ONLY `watch.hot_hours_local`, never targets/caps/mode.

## T12 — 2FA broker live validation 🔴
**Gate:** 2.4
**Trigger:** T2 done.
**Do:** force an Apple ID re-login mid-drill and confirm the Telegram
code-relay path completes login end-to-end in < 5 min. Harden the 2FA
input selectors in `buyer._maybe_signin_recovery` against Apple's actual
markup (currently best-effort guesses).
**Accept:** one forced re-login recovered via Telegram relay.
**Files:** `buyer.py` (2FA selectors).

---

## Suggested order once G0 lands

`T3 (caps)` ∥ `T1 (selectors)` → `T2 (drill+verify)` → `T4 (soak)` ∥
`T5 (CI)` → `T12 (2FA)` → `T6 (live rehearsal)` → arm confirm mode.
`T7/T8/T9/T10/T11` are hardening, done in parallel as capacity allows.

## What a future model session should read first

1. `CLAUDE.md` (invariants — especially: no LLM in the hot path,
   targets.yaml is spending authority, guardrails at strike time).
2. `../.plans/mac-studio-refurb-sniper-goal.md` (the gate the ticket maps to).
3. The ticket here.
4. The specific module named in the ticket.
