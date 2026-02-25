# Valheim Docker Controller Discord Bot

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

2. **Create Permissions File**:
   Create an empty JSON file to store permissions.
   ```bash
   echo "{}" > permissions.json
   ```

3. **Configure `docker-compose.yml`**:
   Ensure the environment variables are set correctly. You can modify the `environment` section in `docker-compose.yml` or use an `.env` file.

   | Variable | Description | Default |
   | :--- | :--- | :--- |
   | `BOT_TOKEN` | **Required**. Your Discord Bot Token. | - |
   | `ALLOWED_CONTAINERS` | The name of the container this bot controls. | - |
   | `DEFAULT_ALLOWED_ROLES` | Comma-separated list of Discord role names allowed to use control commands initially. | `ServerAdmin` |
   | `DISCORD_GUILD_ID` | **Recommended**. The ID of your Discord server. If set, the bot ignores commands from other servers/DMs. | `0` (Disabled) |
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

### Control Commands
Requires specific permissions (default: `ServerAdmin` role).

- `!start`: Starts the container.
- `!stop`: Announces shutdown, waits for delay, then stops the container.
- `!restart`: Announces restart, waits for delay, then restarts the container.

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
Returns the status of all allowed containers.
