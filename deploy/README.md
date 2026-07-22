# Deploying the Mac Studio watcher

Runs the availability watcher as a managed service that starts on boot and
restarts on crash. Detection-only — no browser, no purchasing. Must run on
an always-on machine with a **residential** internet connection (apple.com
blocks datacenter IPs).

## One command

```bash
git clone <hermes-agent repo> && cd hermes-agent
git checkout claude/mac-studio-purchase-bot-w04mkm
./deploy/install.sh
```

That script (idempotent — safe to re-run after a `git pull`):
1. creates `.venv` and installs deps (`httpx pyyaml curl_cffi`),
2. scaffolds `~/.mac_studio_sniper/env` (chmod 600),
3. smoke-tests the package,
4. installs and starts the service for your OS:
   - **Linux** → systemd user unit `mac-studio-sniper.service` (+ enables
     lingering so it survives logout),
   - **macOS** → launchd agent `com.macstudiosniper.watch`.

Use `./deploy/install.sh --no-service` to set up the venv only and run it
yourself.

## Phone alerts (optional)

Console/log alerts work with zero config. For phone pings, edit
`~/.mac_studio_sniper/env`:

```bash
# @BotFather -> /newbot -> token, then:
.venv/bin/python -m mac_studio_sniper telegram-setup --token <TOKEN>
# put the printed values in ~/.mac_studio_sniper/env, then restart the service
```

Restart after editing env:
- Linux: `systemctl --user restart mac-studio-sniper`
- macOS: `launchctl unload ~/Library/LaunchAgents/com.macstudiosniper.watch.plist && launchctl load ~/Library/LaunchAgents/com.macstudiosniper.watch.plist`

## Watch what it finds

```bash
.venv/bin/python -m mac_studio_sniper report    # what M3 Ultras have appeared
.venv/bin/python -m mac_studio_sniper status    # poll health / any bot-blocks
```

## Service management

| | Linux (systemd) | macOS (launchd) |
|---|---|---|
| status | `systemctl --user status mac-studio-sniper` | `launchctl list \| grep macstudio` |
| logs | `journalctl --user -u mac-studio-sniper -f` | `tail -f ~/.mac_studio_sniper/watch.log` |
| stop | `systemctl --user stop mac-studio-sniper` | `launchctl unload …/com.macstudiosniper.watch.plist` |
| start | `systemctl --user start mac-studio-sniper` | `launchctl load …/com.macstudiosniper.watch.plist` |

## macOS: don't let it sleep

A sleeping Mac doesn't poll. On the always-on Mac, keep it awake on power:

```bash
sudo pmset -c sleep 0        # never sleep while plugged in
```

or System Settings → Battery/Energy → prevent automatic sleeping on power.

## Emergency stop

```bash
touch ~/.mac_studio_sniper/KILL     # watcher halts within one loop
rm ~/.mac_studio_sniper/KILL        # resume
```

## When you move past detection

This deploys only the watcher. The full auto-purchase stack (checkout
drills, guardrails, supervisor) is documented in
`../mac_studio_sniper/RUNBOOK.md` and adds its own timers; it needs
`playwright install chromium` and the G0 recon step first.
