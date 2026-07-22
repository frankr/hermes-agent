# Phase 0: Just prove the machines show up

Before building any purchase machinery, confirm with your own eyes that
refurbished **Mac Studio M3 Ultra** units actually appear — and how often.
This is detection only: no checkout, no flightplan, no buying. You can
stop here as long as you like and decide later whether the rest is worth it.

Runs from any always-on machine on a **residential** connection (apple.com
blocks datacenter IPs).

## Setup (~2 min)

```bash
git clone <hermes-agent repo> && cd hermes-agent
git checkout claude/mac-studio-purchase-bot-w04mkm
python3 -m venv .venv && source .venv/bin/activate
pip install httpx pyyaml curl_cffi        # NOTE: no playwright needed for watch-only
```

## Run the watcher

```bash
python -m mac_studio_sniper watch \
    --targets mac_studio_sniper/targets.availability.yaml
```

That's it. This config is deliberately broad — **no price cap, no RAM
filter** — so you see every M3 Ultra that appears and get the full picture
of cadence. It prints to the console; leave it running (a terminal, tmux,
or `nohup … &`). Every sighting is logged to
`~/.mac_studio_sniper/state.sqlite`.

### Optional: phone alerts

Console-only is fine to start. To also get pinged on your phone:

```bash
# @BotFather → /newbot → copy token → message the bot once, then:
python -m mac_studio_sniper telegram-setup --token <TOKEN>
export SNIPER_TELEGRAM_BOT_TOKEN=<TOKEN>
export SNIPER_TELEGRAM_CHAT_ID=<printed id>
# restart watch in the same shell so it picks up the env vars
```

## Check what's shown up

Any time, in another shell:

```bash
python -m mac_studio_sniper report
```

Example output once units have appeared:

```
M3 Ultra sightings: 2 distinct SKU(s)

  part            RAM      price  seen  first → last
  G0MDXLL/A     512GB     $7,999     4  02-14 05:12 → 02-14 05:41
  G0MDYLL/A     256GB     $5,999    11  02-11 04:58 → 02-16 06:03

2 distinct SKU(s) first appeared over 3.0 day(s); watching for 6.0 day(s).
  512GB seen: 1 SKU(s)   256GB seen: 1 SKU(s)
```

`report` reads the raw sighting log directly — no price/RAM filtering —
so it answers "is it showing up, and how often" honestly, including SKUs
you'd never actually buy. Run `python -m mac_studio_sniper status` for
poll health (success rate, any bot-blocks).

## What you learn here

After a week or two you'll know:
- **Do 512GB / 256GB M3 Ultras appear at all**, and at what prices.
- **How often**, and roughly **what time of day** (early-morning PT is the
  historical pattern; `learn-windows` can crunch this once you have data).
- **Whether polling from your IP stays clean** (`status` shows any blocks).

If they're showing up often enough to bother chasing, come back and we
finish the build (checkout recon → drills → confirm-mode buying). If they
basically never appear, you've spent 2 minutes instead of days finding out.

## When you're ready for more

The full system (auto-purchase, guardrails, supervisor) is already built
behind this — see `RUNBOOK.md` for the complete bring-up, and
`FOLLOWUPS.md` (Linear BUI-26…37) for the remaining wiring. Switch from
`targets.availability.yaml` to `targets.yaml` (specific RAM + price caps)
when you move past detection.
