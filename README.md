# Discord Docker Controller Bot

[![Tests](https://github.com/brandonfhall/discord-docker-server-bot/actions/workflows/tests.yaml/badge.svg)](https://github.com/brandonfhall/discord-docker-server-bot/actions/workflows/tests.yaml)
[![Docker Hub](https://img.shields.io/docker/v/brandonh317/discord-docker-bot?label=Docker%20Hub)](https://hub.docker.com/r/brandonh317/discord-docker-bot)

A Discord bot that controls Docker containers (game servers, services, etc.). Every command works three ways — the `!` prefix (`!start`), an @-mention (`@Bot start`), and native slash commands (`/start`) — so you can manage start, stop, restart, and in-game announcements from Discord with role-based permissions.

## Features

- **Triple Command Interface** — Every command works as a `!` prefix command, an `@Bot` mention, and a native `/` slash command.
- **Multi-Container Control** — Start, stop, and restart one or more Docker containers.
- **Graceful Shutdowns** — Announces shutdowns/restarts in Discord and in-game, then waits a configurable delay before acting.
- **Immediate Stop/Restart** — `!stop now` and `!restart now` bypass the countdown for emergencies (separate permissions).
- **Container Logs** — View recent container logs directly in Discord with `!logs`.
- **Resource Stats** — Monitor container CPU and memory usage with `!stats`.
- **Crash Alerting** — Automatic Discord notifications when a container unexpectedly stops.
- **Command History** — Audit log of all bot commands with `!history`.
- **Maintenance Mode** — Temporarily disable all container commands with `!maintenance on`. Persists across bot restarts; cleared only by an explicit `!maintenance off`.
- **Command Cooldowns** — Per-user rate limiting to prevent command spam.
- **Role-Based Permissions** — Restrict commands to specific Discord roles, manageable live via `!perm` commands.
- **Guild & Channel Locking** — Restrict the bot to a specific Discord server and/or set of channels.
- **Status API** — HTTP endpoint exposing container status, permissions, and recent logs.
- **Security** — Strict container name allowlist, input sanitization, and log token redaction.

## Quick Start

```bash
cp .env.example .env   # fill in BOT_TOKEN, ALLOWED_CONTAINERS, and DISCORD_GUILD_ID
docker compose up -d --build
```

## Discord Bot Setup

### 1. Create a Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** and name it.
3. Go to **Bot** tab, click **Add Bot**, then **Reset Token** — copy it as your `BOT_TOKEN`.
4. Under **Privileged Gateway Intents**, enable **Message Content Intent** (required for `!` prefix commands) and **Server Members Intent** (required for role-based permission checks). The bot will fail to start with `discord.errors.PrivilegedIntentsRequired` if either is left disabled.

### 2. Invite the Bot

1. Go to **OAuth2 > URL Generator**.
2. Scopes: `bot` **and** `applications.commands` (the second scope is required for `/` slash commands to appear — without it only `!` and `@Bot` mention commands work).
3. Permissions: `Send Messages`, `Read Message History`, `View Channels` (add `Mention Everyone` if using `ANNOUNCE_ROLE_ID`).
4. Open the generated URL to invite. If you're adding slash commands to a bot that's already in your server, re-open the invite URL with the `applications.commands` scope once to authorize them.

### 3. Get IDs

Enable **Developer Mode** in Discord (User Settings > Advanced) to copy IDs by right-clicking:

| What | Where to find | Used for |
|---|---|---|
| Server (Guild) ID | Right-click server name | `DISCORD_GUILD_ID` |
| Channel ID | Right-click channel | `ANNOUNCE_CHANNEL_ID`, `ALLOWED_CHANNEL_IDS` |
| Role ID | Server Settings > Roles > right-click | `ANNOUNCE_ROLE_ID` |

## Environment Variables

| Variable | Required | Description | Default |
|---|---|---|---|
| `BOT_TOKEN` | Yes | Discord bot token | — |
| `ALLOWED_CONTAINERS` | Yes | Comma-separated container names to control | — |
| `DISCORD_GUILD_ID` | Yes (or `ALLOW_ANY_GUILD`) | Discord server (guild) ID the bot is locked to | — |
| `ALLOW_ANY_GUILD` | | Explicitly allow the bot to run without a guild lock — see [Security](#security) before setting this | `false` |
| `DEFAULT_ALLOWED_ROLES` | | Roles allowed to use commands on first run | `ServerAdmin` |
| `ANNOUNCE_CHANNEL_ID` | | Channel for shutdown/restart announcements | `0` (command channel) |
| `ANNOUNCE_ROLE_ID` | | Role to @mention during announcements | `0` (none) |
| `ALLOWED_CHANNEL_IDS` | | Comma-separated channel IDs where commands work | (all) |
| `STATUS_TOKEN` | | Auth token for the `/status` HTTP API | (open) |
| `CONTAINER_MESSAGE_CMD` | | Shell command template for in-game messages | `echo "Message: {message}"` |
| `SHUTDOWN_DELAY` | | Seconds between announcement and stop/restart | `300` |
| `STATUS_PORT` | | Port for the HTTP status API | `8000` |
| `DOCKER_MAX_WORKERS` | | Max concurrent Docker operations | `2` |
| `LOG_LEVEL` | | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) | `INFO` |
| `PERMISSIONS_FILE` | | Path to permissions JSON file | `data/permissions.json` |
| `LOG_FILE` | | Path to log file | `data/bot.log` |
| `COMMAND_COOLDOWN` | | Per-user command cooldown in seconds | `5` |
| `CRASH_CHECK_INTERVAL` | | Seconds between container status polls for crash alerting | `30` |
| `CRASH_ALERT_CHANNEL_ID` | | Channel for crash alerts (falls back to `ANNOUNCE_CHANNEL_ID`) | `0` |
| `HISTORY_FILE` | | Path to command history JSON file | `data/history.json` |
| `MAINTENANCE_FILE` | | Path to persisted maintenance-mode state (`{mode, reason}`) | `data/maintenance.json` |
| `HEALTHCHECK_POLL_INTERVAL` | | Seconds between health polls after `!start`, for containers with a Docker `HEALTHCHECK` | `5` |
| `HEALTHCHECK_MAX_WAIT` | | Seconds `!start` watches a healthcheck before giving up (`0` = no limit) | `1800` |

See [.env.example](.env.example) for a copy-paste template.

## Commands

Every command below can be invoked three ways: the `!` prefix (`!start`), by mentioning the bot (`@Bot start`), or as a native slash command (`/start`, chosen from Discord's command picker — no prefix or mention needed). The tables use `!` for brevity. Container name is optional when only one container is configured.

For slash commands, the optional container and the `now` flag are separate fields (e.g. `/stop container:myserver now:now`); the `!`/mention forms keep the free-form syntax shown below (`!stop myserver now`).

### Control

| Command | Permission | Description |
|---|---|---|
| `!start [container]` | `start` | Start the container. If it defines a Docker `HEALTHCHECK`, the bot reports "started" only once the healthcheck reports healthy (see `HEALTHCHECK_POLL_INTERVAL`/`HEALTHCHECK_MAX_WAIT`); otherwise it reports success as soon as the container process launches. Warns (but doesn't block) if a stop/restart countdown is already scheduled for the container |
| `!stop [container]` | `stop` | Announce shutdown, wait for delay, then stop |
| `!stop [container] now` | `stop` + `stop_now` | Immediately stop (skips countdown, cancels pending) |
| `!restart [container]` | `restart` | Announce restart, wait for delay, then restart |
| `!restart [container] now` | `restart` + `restart_now` | Immediately restart (skips countdown, cancels pending) |
| `!cancel` | `cancel` | Cancel all pending stop/restart countdowns across every container |
| `!announce [container] <message>` | `announce` | Send a message to the server console |
| `!status [container]` | — | Show container status, Docker healthcheck status (if configured), and any pending stop/restart countdown |
| `!guide` | — | Show a quick command reference |

### Info

| Command | Permission | Description |
|---|---|---|
| `!logs [container] [lines]` | `logs` | View recent container logs (max 50 lines) |
| `!stats [container]` | `stats` | Show container CPU and memory usage |
| `!history [count]` | `history` | View recent command history (max 25 entries) |

### Admin

| Command | Permission | Description |
|---|---|---|
| `!maintenance on [reason]` | `maintenance` | Enable maintenance mode (blocks all container commands, cancels pending countdowns). Persisted to `MAINTENANCE_FILE`, so it survives a bot restart. |
| `!maintenance off` | `maintenance` | Disable maintenance mode. Also persisted — this is the only way to clear it. |
| `!maintenance` | `maintenance` | Show current maintenance mode status |
| `!perm list` | Admin | List roles allowed for each action |
| `!perm add <action> <role>` | Admin | Grant a role permission for an action |
| `!perm remove <action> <role>` | Admin | Revoke a role's permission |

Valid actions: `start`, `stop`, `stop_now`, `restart`, `restart_now`, `cancel`, `announce`, `logs`, `stats`, `maintenance`, `history`.

Discord Administrators bypass all permission checks.

> **Note:** permissions are matched by role **name**, not role ID. Renaming a Discord role silently revokes every grant tied to it, with no warning — re-grant it with `!perm add` after renaming a role.

## HTTP Status API

**`GET /healthz`** — Unauthenticated liveness check. Returns `{"ok": true}` whenever the process is running. Used by the Docker healthcheck; also safe to use for external uptime monitoring.

**`GET /status`** — Returns, as JSON: per-container status and Docker healthcheck state, the full role-permission map, and the last 50 log lines (which include Discord usernames, user IDs, channel names, and every command typed). Treat this endpoint as sensitive.

Each entry under `containers` is `{"status": "<docker status>", "health": "<healthy|unhealthy|starting|null>"}`. `health` is `null` for containers that don't define a `HEALTHCHECK` — most containers won't, and that's expected, not an error.

Authentication (when `STATUS_TOKEN` is set):
- Header: `X-Auth-Token: <token>` (preferred — doesn't land in proxy/access logs)
- Query param: `/status?token=<token>`

If `STATUS_TOKEN` is unset, `/status` has no authentication at all. The default compose setup binds the status port to the Docker host's loopback interface only (`127.0.0.1:8000:8000`); set `STATUS_TOKEN` before changing that to expose the port beyond localhost.

**`GET /`** — Redirects to `/status`.

## In-Game Announcements

Configure `CONTAINER_MESSAGE_CMD` with a `{message}` placeholder. The message is sanitized to alphanumeric + basic punctuation, truncated to 100 characters.

**Valheim (screen):**
```
CONTAINER_MESSAGE_CMD=screen -S valheim -p 0 -X stuff "say {message}\015"
```

**Minecraft (RCON):**
```
CONTAINER_MESSAGE_CMD=rcon-cli say "{message}"
```

> **Note on argument injection:** the sanitizer allows `-` in messages so players can type dashes naturally. This means a message starting with `-` could be interpreted as a flag by the downstream command (e.g., `rcon-cli say -n`). When your template invokes a tool that takes flags, use an explicit `--` separator to stop flag parsing:
> ```
> CONTAINER_MESSAGE_CMD=rcon-cli say -- "{message}"
> ```

## Development

For internal architecture, module boundaries, and runtime model, see [ARCHITECTURE.md](ARCHITECTURE.md). Contributor conventions are in [CLAUDE.md](CLAUDE.md).

### Running Tests

```bash
pip install -r requirements-dev.txt
export PYTHONPATH=.
pytest -v tests/
```

No Docker daemon required — all Docker calls are mocked.

### Local Dev

```bash
docker compose -f docker-compose.dev.yml up --build
```

Mounts `src/` for live code updates (container restart required to pick up changes).

## Security

- The bot requires `/var/run/docker.sock` access, granting full Docker daemon control on the host. Run only on trusted hosts.
- `DISCORD_GUILD_ID` locks the bot to a single Discord server and is required (set `ALLOW_ANY_GUILD=true` only if you understand the risk below). Without a guild lock, anyone able to invite the bot to a server they control would gain full container control there: role permissions are matched by **name**, and a Discord Administrator in that server always bypasses permission checks entirely.
- Keep `BOT_TOKEN` and `STATUS_TOKEN` secret.
- The `/status` endpoint (see [HTTP Status API](#http-status-api)) exposes recent log lines and the permission map, and has no authentication unless `STATUS_TOKEN` is set. The bundled `docker-compose.yml` binds it to the Docker host's loopback interface only — set `STATUS_TOKEN` before exposing it further.
- Container names are validated against a strict allowlist regex before any Docker call.
- All announcement messages are sanitized before being passed to `exec_run`. See the [In-Game Announcements](#in-game-announcements) section for a note on argument injection in command templates.
- Sensitive tokens are redacted from all log output.

### Entrypoint and Docker socket permissions

The entrypoint detects the GID of `/var/run/docker.sock` at runtime and adds `botuser` to a matching group so it can reach the socket without running as root. On hosts where the socket is owned by GID 0 (root group — common on some Linux distributions), the entrypoint adds `botuser` to the `root` group. This is less restrictive than a dedicated `docker` group. If this concerns you, run the bot behind a [docker-socket-proxy](#hardening-restricting-docker-socket-access) so the socket is never exposed directly.

### Hardening: restricting Docker socket access

Mounting the raw Docker socket is convenient but gives the container root-equivalent access to the host. For stricter deployments, put a [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) in front of the socket and point the bot at it — you can expose only the `containers` endpoint the bot needs and deny everything else:

```yaml
services:
  docker-proxy:
    image: tecnativa/docker-socket-proxy
    environment:
      CONTAINERS: 1        # list/inspect/start/stop/restart
      POST: 1              # required for start/stop/restart/exec
      EXEC_CREATE: 1       # required for !announce
      EXEC_START: 1
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped

  discord-bot:
    # ...
    environment:
      - DOCKER_HOST=tcp://docker-proxy:2375
    # drop the docker.sock volume; use the proxy instead
```

This narrows the blast radius from "any exploit → full host root" to "any exploit → the whitelisted container operations".

## License

MIT
