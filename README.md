# Discord Docker Controller Bot

[![Tests](https://github.com/brandonfhall/discord-docker-server-bot/actions/workflows/tests.yaml/badge.svg)](https://github.com/brandonfhall/discord-docker-server-bot/actions/workflows/tests.yaml)
[![Docker Hub](https://img.shields.io/docker/v/brandonh317/discord-docker-bot?label=Docker%20Hub)](https://hub.docker.com/r/brandonh317/discord-docker-bot)

A Discord bot that controls Docker containers (game servers, services, etc.) through `!` prefix commands. Manage start, stop, restart, and in-game announcements — all from Discord with role-based permissions.

## Features

- **Multi-Container Control** — Start, stop, and restart one or more Docker containers.
- **Graceful Shutdowns** — Announces shutdowns/restarts in Discord and in-game, then waits a configurable delay before acting.
- **Immediate Stop/Restart** — `!stop now` and `!restart now` bypass the countdown for emergencies (separate permissions).
- **Container Logs** — View recent container logs directly in Discord with `!logs`.
- **Resource Stats** — Monitor container CPU and memory usage with `!stats`.
- **Crash Alerting** — Automatic Discord notifications when a container unexpectedly stops.
- **Command History** — Audit log of all bot commands with `!history`.
- **Maintenance Mode** — Temporarily disable all container commands with `!maintenance on`.
- **Command Cooldowns** — Per-user rate limiting to prevent command spam.
- **Role-Based Permissions** — Restrict commands to specific Discord roles, manageable live via `!perm` commands.
- **Guild & Channel Locking** — Restrict the bot to a specific Discord server and/or set of channels.
- **Status API** — HTTP endpoint exposing container status, permissions, and recent logs.
- **Security** — Strict container name allowlist, input sanitization, and log token redaction.

## Quick Start

```bash
cp .env.example .env   # fill in BOT_TOKEN and ALLOWED_CONTAINERS
docker compose up -d --build
```

## Discord Bot Setup

### 1. Create a Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** and name it.
3. Go to **Bot** tab, click **Add Bot**, then **Reset Token** — copy it as your `BOT_TOKEN`.
4. Enable **Message Content Intent** (required for `!` prefix commands).

### 2. Invite the Bot

1. Go to **OAuth2 > URL Generator**.
2. Scopes: `bot`.
3. Permissions: `Send Messages`, `Read Message History`, `View Channels` (add `Mention Everyone` if using `ANNOUNCE_ROLE_ID`).
4. Open the generated URL to invite.

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
| `DEFAULT_ALLOWED_ROLES` | | Roles allowed to use commands on first run | `ServerAdmin` |
| `DISCORD_GUILD_ID` | | Lock bot to one Discord server | `0` (any) |
| `ANNOUNCE_CHANNEL_ID` | | Channel for shutdown/restart announcements | `0` (command channel) |
| `ANNOUNCE_ROLE_ID` | | Role to @mention during announcements | `0` (none) |
| `ALLOWED_CHANNEL_IDS` | | Comma-separated channel IDs where commands work | (all) |
| `STATUS_TOKEN` | | Auth token for the `/status` HTTP API | (open) |
| `CONTAINER_MESSAGE_CMD` | | Shell command template for in-game messages | `echo "Message: {message}"` |
| `SHUTDOWN_DELAY` | | Seconds between announcement and stop/restart | `300` |
| `STATUS_PORT` | | Port for the HTTP status API | `8000` |
| `DOCKER_MAX_WORKERS` | | Max concurrent Docker operations | `2` |
| `LOG_LEVEL` | | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `PERMISSIONS_FILE` | | Path to permissions JSON file | `data/permissions.json` |
| `LOG_FILE` | | Path to log file | `data/bot.log` |
| `COMMAND_COOLDOWN` | | Per-user command cooldown in seconds | `5` |
| `CRASH_CHECK_INTERVAL` | | Seconds between container status polls for crash alerting | `30` |
| `CRASH_ALERT_CHANNEL_ID` | | Channel for crash alerts (falls back to `ANNOUNCE_CHANNEL_ID`) | `0` |
| `HISTORY_FILE` | | Path to command history JSON file | `data/history.json` |

See [.env.example](.env.example) for a copy-paste template.

## Commands

All commands use the `!` prefix. Container name is optional when only one container is configured.

### Control

| Command | Permission | Description |
|---|---|---|
| `!start [container]` | `start` | Start the container |
| `!stop [container]` | `stop` | Announce shutdown, wait for delay, then stop |
| `!stop [container] now` | `stop` + `stop_now` | Immediately stop (skips countdown, cancels pending) |
| `!restart [container]` | `restart` | Announce restart, wait for delay, then restart |
| `!restart [container] now` | `restart` + `restart_now` | Immediately restart (skips countdown, cancels pending) |
| `!announce [container] <message>` | `announce` | Send a message to the server console |
| `!status [container]` | — | Show container status |
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
| `!maintenance on [reason]` | `maintenance` | Enable maintenance mode (blocks all container commands) |
| `!maintenance off` | `maintenance` | Disable maintenance mode |
| `!maintenance` | `maintenance` | Show current maintenance mode status |
| `!perm list` | Admin | List roles allowed for each action |
| `!perm add <action> <role>` | Admin | Grant a role permission for an action |
| `!perm remove <action> <role>` | Admin | Revoke a role's permission |

Valid actions: `start`, `stop`, `stop_now`, `restart`, `restart_now`, `announce`, `logs`, `stats`, `maintenance`, `history`.

Discord Administrators bypass all permission checks.

## HTTP Status API

**`GET /status`** — Returns container status, permissions, and recent logs as JSON.

Authentication (when `STATUS_TOKEN` is set):
- Header: `X-Auth-Token: <token>`
- Query param: `/status?token=<token>`

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

## Development

### Running Tests

```bash
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
- Keep `BOT_TOKEN` and `STATUS_TOKEN` secret.
- Container names are validated against a strict allowlist regex before any Docker call.
- All announcement messages are sanitized before being passed to `exec_run`.
- Sensitive tokens are redacted from all log output.

## License

MIT
