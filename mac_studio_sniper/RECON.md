# G0 Recon — Operator Checklist (mostly automated)

Everything below must run from **your home machine / residential
connection** — apple.com blocks datacenter traffic, so the dev sandbox
can't do this part. With the recon tooling this is ~30 minutes of your
attention. Gate definitions: `.plans/mac-studio-refurb-sniper-goal.md`.

Only two things are impossible to automate: **your Apple ID sign-in
(2FA)** and **physically walking one checkout** — and for the second, the
tooling records everything for you while you click.

## 0. One-time setup

```bash
git clone <this repo> && cd hermes-agent
python3 -m venv .venv && source .venv/bin/activate
pip install httpx pyyaml curl_cffi playwright
playwright install chromium
```

## 1. Grid capture — gates 0.1 + 0.3  (automated, ~1 min)

```bash
python -m mac_studio_sniper recon-grid
```

This launches a logged-out headless browser, saves `grid.html` + a HAR
under `~/.mac_studio_sniper/recon/`, runs the parser, prints the gate 0.1
verdict, and — if any M3 Ultra units are listed right now — prints
observed prices with **suggested `max_price_usd` caps** for
`targets.yaml` (gate 0.3).

Your only job: eyeball that the printed tile count matches what the page
actually shows (open it in a normal browser tab). If tiles are missing or
`ERROR:` lines print, send me the command output + the saved `grid.html`
(logged-out, no personal data) and I'll patch the parser.

## 2. Checkout walk — gate 0.2  (recorded for you, ~10 min)

```bash
python -m mac_studio_sniper recon-checkout
```

A visible browser opens with a **persistent profile** (your login is kept
there for the watcher/buyer to reuse later). Then:

1. Sign in to apple.com (2FA happens here, once).
2. Pick the **cheapest in-stock refurb item**, any category.
3. Walk: Add to Bag → Check Out → shipping → payment.
4. **Stop on the Place Order page — do not click Place Order.**
5. Close the browser window.

Every element you click is recorded automatically (tag, `data-autom`
attribute — Apple's own test hooks — text, sanitized outerHTML). Input
**values are never captured**, so no card/address data lands in the
selector report. Output:

- `checkout-selectors-<ts>.txt` — **share this**; it seeds flightplan.yaml
- `checkout-<ts>.har` — cookies/PII inside; stays local, never commit

## 3. Price caps — gate 0.3  (semi-automated)

Take the suggestions printed by `recon-grid` (or, if no M3 Ultra was
listed, use list price × 0.88) and edit `max_price_usd` in
`mac_studio_sniper/targets.yaml`. Deliberately manual: this file
authorizes spending.

## 4. Transport viability — gate 0.4  (automated, ~1 h unattended)

On the machine that will run the watcher 24/7:

```bash
python -m mac_studio_sniper probe --count 20 --interval 180
```

**Pass = 20/20 clean parses, 0 block events.** The command prints which
transport it used — make sure it says `curl_cffi`.

## 5. Alerts + account prep  (~5 min)

- Telegram: @BotFather → `/newbot` → copy token → send your bot any
  message → then:

  ```bash
  python -m mac_studio_sniper telegram-setup --token <TOKEN>
  ```

  It discovers your chat id, sends a test message, and prints the two
  `export` lines for the watcher's environment.
- Apple ID: save shipping address + payment card in
  [Account → Payment & Shipping]. Keep 2FA on.
- Start the soak (gate 1.1 clock starts): `python -m mac_studio_sniper watch`
- Live-fire the alert path (gate 1.3): `python -m mac_studio_sniper inject`
  → phone buzzes in <30 s. Check metrics: `python -m mac_studio_sniper status`

## Deliverables back to the repo/agent

| Item | Gate | Contains PII? |
|---|---|---|
| `recon-grid` console output (+ `grid.html` only if it failed) | 0.1/0.3 | no |
| `checkout-selectors-<ts>.txt` | 0.2 | no (values never recorded) |
| Updated `targets.yaml` price caps | 0.3 | no |
| `probe` output from the deployment machine | 0.4 | no |
