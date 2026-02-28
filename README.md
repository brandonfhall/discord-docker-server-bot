# Docker Controller Discord Bot

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
   | `BOT_TOKEN` | **Required**. Your Discord Bot Token. | - |
   | `ALLOWED_CONTAINERS` | The name of the container this bot controls. | - |
   | `DEFAULT_ALLOWED_ROLES` | Comma-separated list of Discord role names allowed to use control commands initially. | `ServerAdmin` |
   | `STATUS_TOKEN` | Token required for the `/status` API. If not set, the API is open (no auth). | `None` |
   | `DISCORD_GUILD_ID` | **Recommended**. The ID of your Discord server. If set, the bot ignores commands from other servers/DMs. | `0` (Disabled) |
   | `ANNOUNCE_CHANNEL_ID` | The ID of the Discord channel to send shutdown/restart announcements to. | `0` (Current Channel) |
   | `ANNOUNCE_ROLE_ID` | The ID of a Discord role to mention (@Role) during announcements. | `0` (None) |
   | `CONTAINER_MESSAGE_CMD` | Shell command to send a message to the container. | `echo "Message: {message}"` |
   | `SHUTDOWN_DELAY` | Time in seconds to wait between announcement and action. | `300` (5 mins) |
   | `STATUS_PORT` | Port for the local HTTP status API. | `8000` |
   | `LOG_LEVEL` | Logging verbosity (`INFO`, `DEBUG`, etc.). | `INFO` |

4. **Run the Bot**:
   ```bash
   docker compose up -d --build
   ```

## Discord Commands

Prefix: `!`

### General
- `!guide`: Shows a simple usage guide.

### Control Commands
Requires specific permissions (default: `ServerAdmin` role).

- `!start <container_name>`: Starts the specified container.
- `!stop <container_name>`: Announces shutdown, waits for delay, then stops the container.
- `!restart <container_name>`: Announces restart, waits for delay, then restarts the container.
- `!announce [container_name] <message>`: Sends an in-game announcement. If only one container is configured, the name is optional.

### Status
- `!status`: Shows the current status (running, exited, etc.) of the container.

### Permission Management
Requires `Administrator` permission in Discord.

- `!perm list`: Lists all roles allowed to perform specific actions.
- `!perm add <action> <role_name>`: Grants a role permission for an action (actions: `start`, `stop`, `restart`).
- `!perm remove <action> <role_name>`: Revokes permission.

## HTTP API

The bot exposes a simple JSON API on port `8000` (mapped in docker-compose).

**GET /status**
Requires authentication via `STATUS_TOKEN` (if configured).

Methods:
1. **Header**: `X-Auth-Token: <YOUR_TOKEN>`
2. **URL Parameter**: `http://localhost:8000/status?token=<YOUR_TOKEN>`

Returns a JSON object containing:
- `containers`: Status of the allowed container.
- `permissions`: Current role permissions.
- `logs`: The most recent 50 log lines.
