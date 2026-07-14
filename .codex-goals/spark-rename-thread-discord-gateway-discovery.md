# /spark-rename-thread Discord Gateway Discovery

Date: 2026-07-14
AgentBoard task: `discover-actual-discord-slash-command-ga-0001`
Agent: `codex-goal-real-discord-thread-rename`

## Finding

The real Discord gateway for Frank's Spark-code Discord thread is Hermes, not the `frankr/spark-code` repo. The usable slash command must be implemented in Hermes' Discord adapter.

## Runtime Evidence

- Live process: `/Users/kai/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace`
- Live repo checkout: `/Users/kai/.hermes/hermes-agent`
- Isolated implementation worktree: `/Users/kai/.hermes/hermes-agent/.worktrees/spark-rename-thread-real-discord`
- Live launch agent plist: `/Users/kai/Library/LaunchAgents/ai.hermes.gateway.plist`
- launchd label: `ai.hermes.gateway`
- ProgramArguments:
  - `/Users/kai/.hermes/hermes-agent/venv/bin/python`
  - `-m`
  - `hermes_cli.main`
  - `gateway`
  - `run`
  - `--replace`
- WorkingDirectory: `/Users/kai/.hermes`
- Logs:
  - `/Users/kai/.hermes/logs/gateway.log`
  - `/Users/kai/.hermes/logs/gateway.error.log`
- Current logs show live Frank Discord thread traffic reaching this gateway, including the July 13 thread-rename correction messages.

## Repo State Notes

The live Hermes checkout is dirty with staged GPT-5.6 upgrade work:

- `plugins/platforms/discord/adapter.py`
- `tests/gateway/test_discord_free_response.py`
- `.codex-goals/`

Those changes pre-date this goal and are unrelated. To avoid corrupting them, `/spark-rename-thread` work is isolated in the new worktree above.

## Command Registration Path

File: `plugins/platforms/discord/adapter.py`

- `DiscordAdapter.connect()` creates a `discord.ext.commands.Bot`.
- If `self._slash_commands` is enabled, `connect()` calls `self._register_slash_commands()` before starting the bot.
- `_register_slash_commands()` registers native Discord app commands on `self._client.tree`.
- After `on_ready`, `_run_post_connect_initialization()` reconciles command registration with Discord.
- Default registration policy is `DISCORD_COMMAND_SYNC_POLICY=safe`.
- Safe sync computes a command fingerprint and uses `_safe_sync_slash_commands()` to create/update/delete global app commands.
- Sync state is persisted at `/Users/kai/.hermes/gateway/discord_command_sync_state.json`.

## Interaction Routing Path

File: `plugins/platforms/discord/adapter.py`

- Existing simple slash commands use `_run_simple_slash()`, which authorizes the interaction, defers ephemerally, builds a `MessageEvent`, and routes into Hermes conversation handling.
- `/thread` is a native special-case command registered by `_register_slash_commands()` and handled by `_handle_thread_create_slash()`.
- Component/button authorization is centralized in `_component_check_auth()` and view classes under `_define_discord_view_classes()`.
- Existing interactive views prove Hermes can render Discord buttons/select menus and receive component callbacks.
- `discord.ui.Modal` is available through the same `discord.py` UI surface and is the right custom-name submit path.

## Implementation Seam

Implement in Hermes:

- Add pure helper functions/classes in a focused module or in the adapter-adjacent testable surface for:
  - scoped AgentBoard fetch from `http://kai-mm.tail10d3ac.ts.net:8771/api/boards/spark-code/tasks`
  - exactly-one active task selection
  - exactly three task-derived option names
  - custom-name validation/truncation for Discord thread names
- Add `/spark-rename-thread` registration in `DiscordAdapter._register_slash_commands()`.
- Add a native handler that:
  - authorizes via `_check_slash_authorization()`
  - refuses non-thread/non-renamable contexts before any rename
  - queries scoped Spark-code AgentBoard route only
  - replies ephemerally with exactly three generated options plus one custom button
  - never calls thread rename during the initial slash invocation
- Add a `SparkThreadRenameView` whose option buttons rename only after explicit click.
- Add a `SparkThreadRenameModal` for custom input; submit validates and renames only after explicit submit.
- Re-check current interaction channel/thread before every rename.

## Tests To Modify/Add

Primary target:

- `tests/gateway/test_discord_slash_commands.py`

Focused coverage:

- slash command registration includes `spark-rename-thread`
- initial invocation replies with one view containing exactly three generated options plus custom and does not call rename
- option selection calls the Discord thread rename method exactly once
- custom button opens a modal, and modal submit calls rename only for valid input
- no active task, multiple active tasks, board lookup failure, invalid custom name, and non-thread context refuse without rename
- scoped AgentBoard URL is used; no `/api/tasks` or `?board=` fallback

## Deploy/Registration Implications

- Code changes alone do not update the live Discord command list until the Hermes gateway process running the new code starts and command sync runs.
- Do not restart the live gateway or force command sync without operator approval.
- Expected safe deploy path after PR review:
  1. Apply/merge the Hermes PR into the live Hermes checkout or deploy worktree.
  2. Restart `ai.hermes.gateway` under launchd so `DiscordAdapter.connect()` registers the new command in memory.
  3. Let default safe command sync reconcile `/spark-rename-thread`, or explicitly set `DISCORD_COMMAND_SYNC_POLICY=safe`.
  4. Watch `/Users/kai/.hermes/logs/gateway.log` for safe sync summary.
  5. Smoke test from a Spark-code Discord thread: invoke `/spark-rename-thread`; verify exactly three options plus custom; click one option only in a disposable/test thread unless operator wants a real rename.

## Spark-code PR #35 Status

The Spark-code branch `/Users/kai/spark-worktrees/slash-command-to-rename-this-discord-thr-0001-direct` contains useful spec/helper context, but it is not the Discord gateway. Any helper-only code there is unreachable from Frank's Discord slash-command UX unless Hermes calls it, and cross-repo imports from Hermes into Spark-code would be inappropriate. The implementation belongs in Hermes.
