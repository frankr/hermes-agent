# /spark-rename-thread Deployment and Registration Notes

Date: 2026-07-14
AgentBoard task: `document-and-verify-deployment-path-for--0001`

## What Changed

Hermes' Discord adapter now registers a native Discord slash command:

- Command: `/spark-rename-thread`
- File: `plugins/platforms/discord/adapter.py`
- Registration seam: `DiscordAdapter._register_slash_commands()`
- Runtime handler: `DiscordAdapter._handle_spark_thread_rename_slash()`
- Component UI:
  - `SparkThreadRenameView`
  - `SparkThreadRenameModal`

The command uses only the scoped Spark-code AgentBoard route:

```text
http://kai-mm.tail10d3ac.ts.net:8771/api/boards/spark-code/tasks
```

It does not use `/api/tasks` or `?board=`.

## Registration Behavior

Hermes registers slash commands in memory during gateway startup, before `discord.py` starts the bot. After Discord `on_ready`, Hermes runs post-connect command reconciliation:

```text
DiscordAdapter._run_post_connect_initialization()
DiscordAdapter._safe_sync_slash_commands()
```

Default policy:

```text
DISCORD_COMMAND_SYNC_POLICY=safe
```

Safe sync compares the local command tree to Discord's global application commands and only mutates changed commands. Sync state is stored at:

```text
/Users/kai/.hermes/gateway/discord_command_sync_state.json
```

## Live Gateway Owner

Current live gateway process:

```text
/Users/kai/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace
```

launchd label:

```text
ai.hermes.gateway
```

launchd plist:

```text
/Users/kai/Library/LaunchAgents/ai.hermes.gateway.plist
```

Logs:

```text
/Users/kai/.hermes/logs/gateway.log
/Users/kai/.hermes/logs/gateway.error.log
```

## Deployment Steps

Do not run these without operator approval.

1. Land or check out the Hermes PR branch in the live Hermes repo.
2. Confirm the live checkout contains the command:

```bash
rg -n "spark-rename-thread|_handle_spark_thread_rename_slash|SparkThreadRenameView" /Users/kai/.hermes/hermes-agent/plugins/platforms/discord/adapter.py
```

3. Restart the supervised gateway so the command is added to the in-memory command tree and safe sync can run:

```bash
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
```

4. Watch logs for connection and command sync:

```bash
tail -f /Users/kai/.hermes/logs/gateway.log
```

Expected signal:

```text
Connected as ...
Safely reconciled ... slash command(s)
```

If safe sync skips because the fingerprint was already synced, that is acceptable after the new command has already registered once.

## Safe Smoke Test

Use a disposable Spark-code Discord thread unless Frank explicitly wants the current thread renamed.

1. Invoke `/spark-rename-thread` from inside a Discord thread.
2. Verify the first response is ephemeral and contains exactly three generated options plus `Custom`.
3. Verify the thread is not renamed immediately.
4. Click one generated option in a disposable thread.
5. Verify only that thread is renamed and the bot posts an ephemeral success response.
6. Invoke again and choose `Custom`; submit a non-empty name.
7. Verify rename happens only after modal submit.

Refusal smoke checks:

- Invoke outside a thread: must refuse without board lookup or rename.
- Temporarily point `SPARK_THREAD_RENAME_BOARD_URL` to an invalid local URL in a non-production process: must report board lookup failure without rename.
- Submit whitespace in the custom modal: must refuse without rename.

## Risk Notes

- Slash-command global sync is rate-limited by Discord. Hermes' default `safe` policy is intentionally conservative.
- A gateway restart is required for live use because the command tree is built at process startup.
- This implementation does not execute any live restart or command sync during development.
- The live Hermes checkout currently has unrelated staged GPT-5.6 upgrade work. Keep the `/spark-rename-thread` PR separate from that branch state.
