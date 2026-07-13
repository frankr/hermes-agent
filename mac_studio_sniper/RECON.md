# G0 Recon — Operator Checklist

Everything below must run from **your home machine / residential
connection** — apple.com blocks datacenter traffic, so the dev sandbox
can't do this part. Budget: ~half a day. Gate definitions:
`.plans/mac-studio-refurb-sniper-goal.md`.

## 0. One-time setup

```bash
git clone <this repo> && cd hermes-agent
python3 -m venv .venv && source .venv/bin/activate
pip install httpx pyyaml curl_cffi        # curl_cffi = Chrome TLS impersonation
```

## 1. Capture the grid page  → gate 0.1

1. Open Chrome **logged OUT of apple.com** (fresh profile is ideal).
2. DevTools (⌥⌘I) → Network tab → check **Preserve log**.
3. Visit `https://www.apple.com/shop/refurbished/mac/mac-studio`.
4. If an "Apple M3 Ultra" filter chip/URL exists, click it and note the
   filtered URL (it becomes a second watch endpoint in `targets.yaml`).
5. Network tab → right-click any request → **Save all as HAR with content**
   → save as `grid.har`.
6. Also save the page itself: ⌘S → "Webpage, HTML Only" → `grid.html`.

Validate immediately:

```bash
python -m mac_studio_sniper parse --har grid.har
python -m mac_studio_sniper parse --html grid.html
```

**Pass = every Mac Studio tile you can see on the page is listed with part
number, chip, and price, exit code 0.** If tiles are missing or errors
print, the parser needs patching for Apple's current schema — send me the
output plus `grid.html` (the logged-out HTML contains no personal data).

Then dry-run the matcher: `python -m mac_studio_sniper targets --har grid.har`

## 2. Capture one checkout walk  → gate 0.2 input

1. Sign in to apple.com with the Apple ID you'll buy with.
2. Pick the **cheapest in-stock refurb item** (any category).
3. With DevTools recording (Preserve log ON), walk: product page →
   **Add to Bag** → bag → **Check Out** → shipping → payment → stop at the
   **Place Order** page. **Do not click Place Order.**
4. Save `checkout.har`, plus a screenshot of each step.
5. For each button/field you interacted with: right-click it → Inspect →
   right-click the highlighted node → Copy → **Copy outerHTML** → paste
   into a text file `checkout-selectors.txt` labeled by step.

⚠️ **`checkout.har` contains your session cookies, address, and card
metadata. Never commit it, never upload it anywhere.** What I need from
you is only `checkout-selectors.txt` + the step-by-step URL list — that's
enough to write `flightplan.yaml` v1.

## 3. Ground the price caps  → gate 0.3

Record any M3 Ultra refurb prices currently visible (or recent history
from tracker sites like refurb.me). Update `max_price_usd` for both
targets in `mac_studio_sniper/targets.yaml`. If nothing is observable,
rule of thumb: Apple US refurb ≈ 15% off original list — set caps at
list × 0.88 to leave margin, and we refine on first sighting.

## 4. Transport viability  → gate 0.4

On the machine that will run the watcher 24/7 (always-on, home network):

```bash
python -m mac_studio_sniper probe --count 20 --interval 180   # ~1 hour
```

**Pass = 20/20 clean parses, 0 block events.** If you see 403s/blocks,
confirm `curl_cffi` is installed (the command prints which transport it
used) before we escalate to browser-based polling.

## 5. Account & alert prep (not gated, but do it now)

- Apple ID: save the shipping address and a payment card in
  [Account → Payment & Shipping]; keep 2FA on (the bot brokers codes to
  you later — don't disable it).
- Telegram: talk to @BotFather → `/newbot` → copy the token. Then message
  your new bot once, and get your chat id from
  `https://api.telegram.org/bot<TOKEN>/getUpdates`. Export:

  ```bash
  export SNIPER_TELEGRAM_BOT_TOKEN=...
  export SNIPER_TELEGRAM_CHAT_ID=...
  # optional SMS: SNIPER_TWILIO_ACCOUNT_SID / _AUTH_TOKEN / _FROM / _TO
  python -m mac_studio_sniper test-alert
  ```

- Start the watcher soak (gate 1.1 begins): 
  `python -m mac_studio_sniper watch` (systemd/launchd unit comes with
  Phase 1 hardening). Check progress anytime with
  `python -m mac_studio_sniper status`.
- Test the live alert path end to end:
  `python -m mac_studio_sniper inject` → phone should buzz in <30 s (gate 1.3).

## Deliverables back to the repo/agent

| Item | Gate | Contains PII? |
|---|---|---|
| `grid.har` / `grid.html` (logged-out) + `parse` output | 0.1 | no |
| `checkout-selectors.txt` + step URL list + screenshots | 0.2 | scrub address/card from screenshots |
| Updated `targets.yaml` price caps | 0.3 | no |
| `probe` output from the deployment machine | 0.4 | no |
