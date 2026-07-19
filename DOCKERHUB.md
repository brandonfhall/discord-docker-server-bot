# Discord Docker Controller Bot

A Discord bot that controls Docker containers (game servers, services, etc.) — every command works as a `!` prefix command, an `@Bot` mention, or a native `/` slash command. Role-based permissions, graceful shutdowns with in-game announcements, crash alerting, container logs/stats, maintenance mode, command history, and an HTTP status API.

**Full documentation:** [GitHub](https://github.com/brandonfhall/discord-docker-server-bot)

## Quick Start

> **Prerequisites:** You need a Discord bot token and the bot invited to your server with both the `bot` and `applications.commands` OAuth scopes (the second is required for `/` slash commands). See the [Discord Bot Setup guide](https://github.com/brandonfhall/discord-docker-server-bot#discord-bot-setup) for step-by-step instructions.

```yaml
services:
  discord-bot:
    image: brandonh317/discord-docker-bot:latest
    container_name: discord_bot
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - bot_data:/app/data
    environment:
      - BOT_TOKEN=your_discord_bot_token
      - ALLOWED_CONTAINERS=my_game_server
      - DEFAULT_ALLOWED_ROLES=ServerAdmin
      - DISCORD_GUILD_ID=your_guild_id
      - SHUTDOWN_DELAY=300
    ports:
      - "127.0.0.1:8000:8000" # loopback only -- see Security note below

volumes:
  bot_data:
```

> **Security:** The bot requires `/var/run/docker.sock` access, granting full Docker daemon control. Run only on trusted hosts and keep your tokens secret. `DISCORD_GUILD_ID` is required and locks the bot to a single server — without it, anyone able to invite the bot to a server they control would gain full container control there. The `/status` API includes recent log lines and the permission map; it's bound to the host's loopback by default and has no auth unless `STATUS_TOKEN` is set — set `STATUS_TOKEN` before changing the port mapping to expose it beyond localhost. For stricter Docker socket access, see the [docker-socket-proxy hardening guide](https://github.com/brandonfhall/discord-docker-server-bot#hardening-restricting-docker-socket-access) in the README.

## Environment Variables

| Variable | Required | Description | Default |
|---|---|---|---|
| `BOT_TOKEN` | Yes | Discord bot token | — |
| `ALLOWED_CONTAINERS` | Yes | Comma-separated container names to control | — |
| `DISCORD_GUILD_ID` | Yes (or `ALLOW_ANY_GUILD`) | Discord server (guild) ID the bot is locked to | — |
| `DEFAULT_ALLOWED_ROLES` | | Roles allowed to use commands on first run | `ServerAdmin` |
| `SHUTDOWN_DELAY` | | Seconds between announcement and stop/restart | `300` |
| `CONTAINER_MESSAGE_CMD` | | Shell command for in-game messages (`{message}` placeholder) | `echo "Message: {message}"` |
| `STATUS_TOKEN` | | Auth token for the `/status` HTTP API | (open) |
| `ANNOUNCE_CHANNEL_ID` | | Channel for shutdown/restart announcements | `0` (command channel) |
| `ANNOUNCE_ROLE_ID` | | Role to @mention during announcements | `0` (none) |
| `ALLOWED_CHANNEL_IDS` | | Comma-separated channel IDs where commands work | (all) |
| `COMMAND_COOLDOWN` | | Per-user command cooldown in seconds | `5` |
| `CRASH_CHECK_INTERVAL` | | Seconds between crash-detection polls | `30` |
| `CRASH_ALERT_CHANNEL_ID` | | Channel for crash alerts | `0` (uses `ANNOUNCE_CHANNEL_ID`) |
| `HEALTHCHECK_POLL_INTERVAL` | | Seconds between health polls after `!start`, for containers with a Docker `HEALTHCHECK` | `5` |
| `HEALTHCHECK_MAX_WAIT` | | Seconds `!start` watches a healthcheck before giving up (`0` = no limit) | `1800` |

See the [full variable list](https://github.com/brandonfhall/discord-docker-server-bot#environment-variables) for additional options (`STATUS_PORT`, `LOG_LEVEL`, `HISTORY_FILE`, `MAINTENANCE_FILE`, etc.).

## Commands

| Command | Description |
|---|---|
| `!start [container]` | Start the container (waits for a healthy Docker healthcheck before reporting "started", if the container defines one). Warns (but doesn't block) if a stop/restart countdown is already scheduled for the container |
| `!stop [container]` | Announce shutdown, wait for delay, then stop |
| `!stop [container] now` | Immediately stop (requires `stop_now` permission) |
| `!restart [container]` | Announce restart, wait for delay, then restart |
| `!restart [container] now` | Immediately restart (requires `restart_now` permission) |
| `!cancel` | Cancel all pending stop/restart countdowns across every container |
| `!announce [container] <message>` | Send a message to the server console |
| `!status [container]` | Show container status, healthcheck status (if configured), and any pending stop/restart countdown |
| `!logs [container] [lines]` | View recent container logs |
| `!stats [container]` | Show container CPU/memory usage |
| `!history [count]` | View recent command history |
| `!maintenance on/off [reason]` | Toggle maintenance mode (enabling cancels pending countdowns). Persists across restarts — cleared only by `!maintenance off` |
| `!guide` | Quick command reference |
| `!perm list` | List role permissions (admin only) |
| `!perm add <action> <role>` | Grant permission (admin only) |
| `!perm remove <action> <role>` | Revoke permission (admin only) |

Every command works three ways — the `!` prefix (`!start`), an `@Bot` mention (`@Bot start`), or a native `/` slash command (`/start`). The table uses `!` for brevity. Container name is optional when only one container is configured.

## In-Game Announcements

Configure `CONTAINER_MESSAGE_CMD` with a `{message}` placeholder:

```bash
# Valheim (screen)
CONTAINER_MESSAGE_CMD=screen -S valheim -p 0 -X stuff "say {message}\015"

# Minecraft (RCON) — use -- to prevent a message starting with '-' being treated as a flag
CONTAINER_MESSAGE_CMD=rcon-cli say -- "{message}"
```

## License

MIT
