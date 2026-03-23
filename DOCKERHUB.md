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

## Adding the Bot to Discord

### 1. Create a Discord Application & Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**, give it a name
3. Go to the **Bot** tab → click **Add Bot**
4. Click **Reset Token** and copy it — this is your `BOT_TOKEN`
5. Scroll down and enable **Message Content Intent** (required for `!` prefix commands)

### 2. Invite the Bot to Your Server

1. In the Developer Portal, go to **OAuth2 → URL Generator**
2. Under **Scopes**, check `bot`
3. Under **Bot Permissions**, check at minimum:
   - `Send Messages`
   - `Read Message History`
   - `View Channels`
   - `Mention Everyone` (only if using `ANNOUNCE_ROLE_ID`)
4. Copy the generated URL and open it in your browser to invite the bot

### 3. Get Your Guild (Server) ID

1. In Discord, enable **Developer Mode**: User Settings → Advanced → Developer Mode
2. Right-click your server name → **Copy Server ID** — this is your `DISCORD_GUILD_ID`

### 4. Get Channel and Role IDs

Developer Mode (enabled above) also lets you copy channel and role IDs.

**Channel IDs** (`ANNOUNCE_CHANNEL_ID`, `ALLOWED_CHANNEL_IDS`):
- Right-click any channel in the sidebar → **Copy Channel ID**

**Role IDs** (`ANNOUNCE_ROLE_ID`):
- Go to **Server Settings → Roles**
- Right-click the role → **Copy Role ID**

> **Security Note**: The bot requires read/write access to `/var/run/docker.sock`, which grants full control over the Docker daemon on the host. Only run this bot on a trusted host and keep your `BOT_TOKEN` and `STATUS_TOKEN` secret.

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
      - bot_data:/app/data
    environment:
      - BOT_TOKEN=your_discord_bot_token
      - ALLOWED_CONTAINERS=my_game_server
      - DEFAULT_ALLOWED_ROLES=ServerAdmin
      - SHUTDOWN_DELAY=300

volumes:
  bot_data:
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
| `DOCKER_MAX_WORKERS` | Max concurrent Docker operations. | `2` |
| `LOG_LEVEL` | Logging verbosity (`INFO`, `DEBUG`). | `INFO` |
| `PERMISSIONS_FILE` | Path to the role permissions JSON file. | `data/permissions.json` |
| `LOG_FILE` | Path to the bot log file. | `data/bot.log` |

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

*   `!start [container_name]`: Start the container. Name required when multiple containers are configured.
*   `!stop [container_name]`: Announce shutdown, wait for delay, then stop.
*   `!restart [container_name]`: Announce restart, wait for delay, then restart.
*   `!status`: Check if the container is running.
*   `!announce [container_name] <message>`: Send a message to the server console/chat.
*   `!guide`: Show a quick command reference in Discord.

## License

MIT License
