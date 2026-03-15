# Docker Controller Discord Bot

**Docker Hub Image:** [brandonh317/discord-docker-bot](https://hub.docker.com/r/brandonh317/discord-docker-bot)

This project provides a Dockerized Discord bot to manage a container running in Docker. It allows you to start, stop, and restart the server from Discord, handles in-game shutdown announcements, and manages permissions via Discord roles. Designed to control a single specific container.

## Features

- **Container Control**: Start, Stop, and Restart the Docker container.
- **Graceful Shutdowns**: Automatically announces shutdowns/restarts in-game and waits for a configurable delay before stopping the container.
- **Permission System**: Restrict commands to specific Discord roles. Admins can manage these permissions dynamically.
- **Guild Locking**: Restrict the bot to a specific Discord server for security.
- **Status API**: Exposes a local HTTP endpoint for monitoring container status.

## Prerequisites

- Docker and Docker Compose installed on the host machine.
- A Discord Bot Token (from the Discord Developer Portal).
- The ID of your Discord server (Guild ID).

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

## Setup

1. **Clone the repository** and navigate to the directory.

2. **Prepare Data Directory**:
   The bot will automatically create a `data/` directory and a `permissions.json` file on the first run.

3. **Configure Environment**:
   Copy the example environment file:
   `cp .env.example .env`
   
   Edit `.env` and ensure the environment variables are set correctly.

   | Variable | Description | Default |
   | :--- | :--- | :--- |
   | `BOT_TOKEN` | **Required**. Your Discord Bot Token. The bot will not start without this. | - |
   | `ALLOWED_CONTAINERS` | **Required**. Comma-separated container name(s) this bot may control. The bot will not start without this. | - |
   | `DEFAULT_ALLOWED_ROLES` | Comma-separated list of Discord role names allowed to use control commands initially. | `ServerAdmin` |
   | `STATUS_TOKEN` | Token required for the `/status` API. If not set, the API is open (no auth). | `None` |
   | `DISCORD_GUILD_ID` | **Recommended**. The ID of your Discord server. If set, the bot ignores commands from other servers/DMs. | `0` (Disabled) |
   | `ANNOUNCE_CHANNEL_ID` | The ID of the Discord channel to send shutdown/restart announcements to. | `0` (Current Channel) |
   | `ANNOUNCE_ROLE_ID` | The ID of a Discord role to mention (@Role) during announcements. | `0` (None) |
   | `ALLOWED_CHANNEL_IDS` | Comma-separated list of Channel IDs where commands are allowed. If blank, all channels are allowed. | `None` |
   | `CONTAINER_MESSAGE_CMD` | Shell command to send a message to the container. | `echo "Message: {message}"` |
   | `SHUTDOWN_DELAY` | Time in seconds to wait between announcement and action. | `300` (5 mins) |
   | `STATUS_PORT` | Port for the local HTTP status API. | `8000` |
   | `DOCKER_MAX_WORKERS` | Max concurrent Docker operations. | `2` |
   | `LOG_LEVEL` | Logging verbosity (`INFO`, `DEBUG`, etc.). | `INFO` |

4. **Run the Bot**:
   ```bash
   docker compose up -d --build
   ```

> **Security Note**: The bot requires read/write access to `/var/run/docker.sock`, which grants full control over the Docker daemon on the host. Only run this bot on a trusted host and ensure your Discord bot token and `STATUS_TOKEN` are kept secret.

## Discord Commands

Prefix: `!`

### General
- `!guide`: Shows a simple usage guide.

### Control Commands
Requires specific permissions (default: `ServerAdmin` role).

- `!start [container_name]`: Starts the container. Container name is required when multiple containers are configured.
- `!stop [container_name]`: Announces shutdown, waits for delay, then stops the container.
- `!restart [container_name]`: Announces restart, waits for delay, then restarts the container.
- `!announce [container_name] <message>`: Sends an in-game announcement.

### Status
- `!status`: Shows the current status (running, exited, etc.) of the container.

### Permission Management
Requires `Administrator` permission in Discord.

- `!perm list`: Lists all roles allowed to perform specific actions.
- `!perm add <action> <role_name>`: Grants a role permission for an action (actions: `start`, `stop`, `restart`, `announce`).
- `!perm remove <action> <role_name>`: Revokes permission.

## HTTP API

The bot exposes a simple JSON API on port `8000` (mapped in docker-compose).

**GET /**
Redirects to `/status`.

**GET /status**
Requires authentication via `STATUS_TOKEN` (if configured).

Methods:
1. **Header**: `X-Auth-Token: <YOUR_TOKEN>`
2. **URL Parameter**: `http://localhost:8000/status?token=<YOUR_TOKEN>`

Returns a JSON object containing:
- `containers`: Status of the allowed container.
- `permissions`: Current role permissions.
- `logs`: The most recent 50 log lines.
