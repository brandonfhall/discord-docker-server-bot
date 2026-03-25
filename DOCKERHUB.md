# Discord Docker Controller Bot

A Discord bot that controls Docker containers (game servers, services, etc.) through `!` prefix commands. Role-based permissions, graceful shutdowns with in-game announcements, and an HTTP status API.

**Full documentation:** [GitHub](https://github.com/brandonfhall/discord-docker-server-bot)

## Quick Start

> **Prerequisites:** You need a Discord bot token and the bot invited to your server. See the [Discord Bot Setup guide](https://github.com/brandonfhall/discord-docker-server-bot#discord-bot-setup) for step-by-step instructions.

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
      - "8000:8000"

volumes:
  bot_data:
```

> **Security:** The bot requires `/var/run/docker.sock` access, granting full Docker daemon control. Run only on trusted hosts and keep your tokens secret.

## Environment Variables

| Variable | Required | Description | Default |
|---|---|---|---|
| `BOT_TOKEN` | Yes | Discord bot token | — |
| `ALLOWED_CONTAINERS` | Yes | Comma-separated container names to control | — |
| `DEFAULT_ALLOWED_ROLES` | | Roles allowed to use commands on first run | `ServerAdmin` |
| `DISCORD_GUILD_ID` | | Lock bot to one Discord server | `0` (any) |
| `SHUTDOWN_DELAY` | | Seconds between announcement and stop/restart | `300` |
| `CONTAINER_MESSAGE_CMD` | | Shell command for in-game messages (`{message}` placeholder) | `echo "Message: {message}"` |
| `STATUS_TOKEN` | | Auth token for the `/status` HTTP API | (open) |
| `ANNOUNCE_CHANNEL_ID` | | Channel for shutdown/restart announcements | `0` (command channel) |
| `ANNOUNCE_ROLE_ID` | | Role to @mention during announcements | `0` (none) |
| `ALLOWED_CHANNEL_IDS` | | Comma-separated channel IDs where commands work | (all) |

See the [full variable list](https://github.com/brandonfhall/discord-docker-server-bot#environment-variables) for additional options (`STATUS_PORT`, `LOG_LEVEL`, etc.).

## Commands

| Command | Description |
|---|---|
| `!start [container]` | Start the container |
| `!stop [container]` | Announce shutdown, wait for delay, then stop |
| `!stop [container] now` | Immediately stop (requires `stop_now` permission) |
| `!restart [container]` | Announce restart, wait for delay, then restart |
| `!announce [container] <message>` | Send a message to the server console |
| `!status [container]` | Show container status |
| `!guide` | Quick command reference |
| `!perm list` | List role permissions (admin only) |
| `!perm add <action> <role>` | Grant permission (admin only) |
| `!perm remove <action> <role>` | Revoke permission (admin only) |

Container name is optional when only one container is configured.

## In-Game Announcements

Configure `CONTAINER_MESSAGE_CMD` with a `{message}` placeholder:

```bash
# Valheim (screen)
CONTAINER_MESSAGE_CMD=screen -S valheim -p 0 -X stuff "say {message}\015"

# Minecraft (RCON)
CONTAINER_MESSAGE_CMD=rcon-cli say "{message}"
```

## License

MIT
