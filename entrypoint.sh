#!/bin/sh
set -e

# If running as root (default), set up non-root user with Docker socket access
if [ "$(id -u)" = "0" ]; then
    # Detect the GID of the Docker socket
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo "")

    if [ -n "$DOCKER_GID" ] && [ "$DOCKER_GID" != "0" ]; then
        # Create a group with the same GID as the Docker socket
        groupadd -g "$DOCKER_GID" -o dockersock 2>/dev/null || true
        usermod -aG dockersock botuser
    elif [ -n "$DOCKER_GID" ] && [ "$DOCKER_GID" = "0" ]; then
        # Socket owned by root group -- botuser needs root group membership
        usermod -aG root botuser
    fi

    # Fix data dir ownership
    chown -R botuser:botuser /app/data 2>/dev/null || true

    # Re-exec as botuser
    exec gosu botuser "$@"
fi

# If already non-root, just exec
exec "$@"
