# Discord Docker Controller Bot

A secure, lightweight Discord bot designed to control a specific Docker container (such as a game server) directly from Discord. It supports starting, stopping, and restarting the container, managing permissions via Discord roles, and sending in-game announcements.

**GitHub Repository:** [https://github.com/brandonfhall/discord-docker-server-bot](https://github.com/brandonfhall/discord-docker-server-bot)

## Features

*   **Container Control**: Start, Stop, and Restart a specific container via Discord commands.
*   **Graceful Shutdowns**: Automatically sends announcements (Discord & in-game) and waits for a configurable delay before stopping the container.
*   **Role-Based Permissions**: Restrict control commands to specific Discord roles (e.g., `ServerAdmin`).
*   **Status API**: Exposes a local HTTP endpoint for monitoring container status.
*   **Security**:
    *   Strict input sanitization to prevent shell injection.
    *   Can be locked to a specific Discord Guild ID.
    *   Only controls containers explicitly allowed in the configuration.

## Quick Start

### Docker Compose

```yaml
services:
  discord-bot:
    image: brandonh317/discord-docker-bot:latest
    container_name: discord_bot
    restart: unless-stopped
    volumes:
      # Required: Allows the bot to control the host docker daemon
      - /var/run/docker.sock:/var/run/docker.sock
      # Optional: Persist logs and permissions
      - ./bot-data:/app/data
    environment:
      - BOT_TOKEN=your_discord_bot_token
      - ALLOWED_CONTAINERS=my_game_server
      - DEFAULT_ALLOWED_ROLES=ServerAdmin
      - SHUTDOWN_DELAY=300
```

### Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `BOT_TOKEN` | **Required**. Your Discord Bot Token. The bot will not start without this. | - |
| `ALLOWED_CONTAINERS` | **Required**. Comma-separated container name(s) to control (exact match). The bot will not start without this. | - |
| `DEFAULT_ALLOWED_ROLES` | Comma-separated list of roles allowed to use commands initially. | `ServerAdmin` |
| `SHUTDOWN_DELAY` | Time in seconds to wait between announcement and stop/restart. | `300` |
| `CONTAINER_MESSAGE_CMD` | Shell command to send a message to the container. Use `{message}` as placeholder. | `echo "Message: {message}"` |
| `STATUS_TOKEN` | Token required to access the `/status` HTTP API. If unset, the API is open with no authentication. | `None` (Open) |
| `DISCORD_GUILD_ID` | Optional. Lock the bot to a specific Discord server ID. | `0` (Disabled) |
| `ANNOUNCE_CHANNEL_ID` | Optional. Channel ID for shutdown/restart announcements. | `0` (Current Channel) |
| `ANNOUNCE_ROLE_ID` | Optional. Role ID to @mention during announcements. | `0` (None) |
| `ALLOWED_CHANNEL_IDS` | Optional. Comma-separated list of Channel IDs where commands are allowed. | `None` (All Channels) |
| `STATUS_PORT` | Port for the local HTTP status API. | `8000` |
| `LOG_LEVEL` | Logging verbosity (`INFO`, `DEBUG`). | `INFO` |

## Configuration

### Permissions
The bot creates a `permissions.json` file in the `/app/data` volume. You can manage permissions dynamically from Discord:
*   `!perm list`: View current permissions.
*   `!perm add <action> <role>`: Allow a role to perform an action (`start`, `stop`, `restart`, `announce`).
*   `!perm remove <action> <role>`: Revoke permission.

### In-Game Announcements
To send messages to a game server (like Valheim or Minecraft), configure `CONTAINER_MESSAGE_CMD`.

**Example for Valheim (using screen):**
```bash
CONTAINER_MESSAGE_CMD=screen -S valheim -p 0 -X stuff "say {message}\015"
```

**Example for Minecraft (RCON/Exec):**
```bash
CONTAINER_MESSAGE_CMD=rcon-cli say "{message}"
```

## Commands

Prefix: `!`

*   `!start`: Start the container.
*   `!stop`: Announce shutdown, wait for delay, then stop.
*   `!restart`: Announce restart, wait for delay, then restart.
*   `!status`: Check if the container is running.
*   `!announce <message>`: Send a message to the server console/chat.
*   `!guide`: Show a quick command reference in Discord.

## License

MIT License
