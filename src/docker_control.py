import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple, Optional

import docker
import requests

from .config import ALLOWED_CONTAINERS, CONTAINER_MESSAGE_CMD, DOCKER_MAX_WORKERS


class Result(NamedTuple):
    """Structured result from Docker operations (start, stop, restart, announce)."""

    success: bool
    message: str


_executor = ThreadPoolExecutor(max_workers=DOCKER_MAX_WORKERS)

# Validate container names: alphanumeric, underscore, dot, hyphen only
_VALID_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9_.-]+$")
# Validate messages: Allow alphanumeric, spaces, and basic punctuation only
_VALID_MSG_CHARS = re.compile(r"[^a-zA-Z0-9 .,!?:_\-]")

# Daemon/connection-level failures: docker.errors.DockerException covers the SDK's
# own error hierarchy (including APIError), but a socket that disappears or refuses
# mid-request (dockerd restart, socket-proxy down, permission regression) can also
# surface as a bare requests exception that never gets wrapped in a DockerException --
# empirically confirmed against docker==7.2.0 (connection to a missing unix socket
# raises requests.exceptions.ConnectionError, not docker.errors.DockerException).
# Both must be treated as "the daemon is unreachable", not "unexpected bug" or
# "container not found" -- see M2.
_DAEMON_CONNECTION_ERRORS = (docker.errors.DockerException, requests.exceptions.RequestException)


def _validate_container_name(name: str) -> bool:
    """Check if container name is valid (alphanumeric, underscore, dot, hyphen)."""
    if not name or not isinstance(name, str) or len(name) > 255:
        return False
    return _VALID_CONTAINER_NAME.match(name) is not None


# Global client instance to avoid re-initializing connection on every request
_docker_client = None


def _get_client():
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


def _find_container_by_name(client, name: str):
    try:
        return client.containers.get(name)
    except docker.errors.NotFound:
        return None
    except _DAEMON_CONNECTION_ERRORS:
        # Daemon-level/connection failure -- let it propagate so callers can tell
        # "the daemon is unreachable" apart from "the container doesn't exist"
        # (M2). Every public function below wraps its body to turn this into an
        # honest Result/None/"error" instead of letting it escape to Discord.
        raise
    except Exception as e:
        logging.warning(f"Unexpected error looking up container {name!r}: {e}")
        return None


def _check_allowed(name: str) -> bool:
    if not _validate_container_name(name):
        return False
    if not ALLOWED_CONTAINERS:
        return False
    return name in ALLOWED_CONTAINERS


def start_container(name: str) -> Result:
    if not _check_allowed(name):
        return Result(False, f"container {name} is not allowed")
    try:
        client = _get_client()
        c = _find_container_by_name(client, name)
        if not c:
            return Result(False, f"container {name} not found")
        c.reload()
        if c.status == "running":
            return Result(False, "already running")
        c.start()
        return Result(True, "started")
    except docker.errors.APIError as e:
        return Result(False, f"docker error: {e.explanation or e}")
    except _DAEMON_CONNECTION_ERRORS as e:
        logging.error(f"Docker daemon error starting {name}: {type(e).__name__}: {e}")
        return Result(False, f"docker daemon error: {type(e).__name__}")


def stop_container(name: str, timeout: int = 10) -> Result:
    if not _check_allowed(name):
        return Result(False, f"container {name} is not allowed")
    try:
        client = _get_client()
        c = _find_container_by_name(client, name)
        if not c:
            return Result(False, f"container {name} not found")
        c.reload()
        if c.status != "running":
            return Result(False, "not running")
        c.stop(timeout=timeout)
        return Result(True, "stopped")
    except docker.errors.APIError as e:
        return Result(False, f"docker error: {e.explanation or e}")
    except _DAEMON_CONNECTION_ERRORS as e:
        logging.error(f"Docker daemon error stopping {name}: {type(e).__name__}: {e}")
        return Result(False, f"docker daemon error: {type(e).__name__}")


def restart_container(name: str, timeout: int = 10) -> Result:
    if not _check_allowed(name):
        return Result(False, f"container {name} is not allowed")
    try:
        client = _get_client()
        c = _find_container_by_name(client, name)
        if not c:
            return Result(False, f"container {name} not found")
        c.restart(timeout=timeout)
        return Result(True, "restarted")
    except docker.errors.APIError as e:
        return Result(False, f"docker error: {e.explanation or e}")
    except _DAEMON_CONNECTION_ERRORS as e:
        logging.error(f"Docker daemon error restarting {name}: {type(e).__name__}: {e}")
        return Result(False, f"docker daemon error: {type(e).__name__}")


def container_status(name: str) -> Optional[str]:
    """Return the container's Docker status (e.g. "running", "exited"), None if
    disallowed/not found, or the literal string "error" if the Docker daemon
    itself was unreachable -- deliberately distinct from None/"not found" so
    !status, /status, and crash alerting can each tell a daemon outage apart
    from "the container doesn't exist" (M2)."""
    if not _check_allowed(name):
        return None
    try:
        client = _get_client()
        c = _find_container_by_name(client, name)
        if not c:
            return None
        # refresh
        c.reload()
        return c.status
    except _DAEMON_CONNECTION_ERRORS as e:
        logging.error(f"Docker daemon error checking status for {name}: {type(e).__name__}: {e}")
        return "error"


def container_health(name: str) -> Optional[str]:
    """Return the container's Docker healthcheck status ("starting", "healthy",
    "unhealthy"), or None if the container is disallowed/not found, has no
    healthcheck configured, or the Docker daemon was unreachable -- callers must
    treat None as "no health data", not as an error, since most containers won't
    define one."""
    if not _check_allowed(name):
        return None
    try:
        client = _get_client()
        c = _find_container_by_name(client, name)
        if not c:
            return None
        c.reload()
        return c.attrs.get("State", {}).get("Health", {}).get("Status")
    except _DAEMON_CONNECTION_ERRORS as e:
        logging.error(f"Docker daemon error checking health for {name}: {type(e).__name__}: {e}")
        return None


def container_logs(name: str, lines: int = 50) -> Optional[str]:
    """Fetch the last *lines* lines of container logs."""
    if not _check_allowed(name):
        return None
    try:
        client = _get_client()
        c = _find_container_by_name(client, name)
        if not c:
            return None
    except _DAEMON_CONNECTION_ERRORS as e:
        logging.error(f"Docker daemon error fetching logs for {name}: {type(e).__name__}: {e}")
        return None
    try:
        return c.logs(tail=lines, timestamps=False).decode("utf-8", errors="replace")
    except Exception as e:
        logging.error(f"Error fetching logs for {name}: {e}")
        return None


def container_stats(name: str) -> Optional[dict]:
    """Return a snapshot of CPU and memory usage for a container."""
    if not _check_allowed(name):
        return None
    try:
        client = _get_client()
        c = _find_container_by_name(client, name)
        if not c:
            return None
        c.reload()
    except _DAEMON_CONNECTION_ERRORS as e:
        logging.error(f"Docker daemon error fetching stats for {name}: {type(e).__name__}: {e}")
        return None
    if c.status != "running":
        return {"status": c.status}
    try:
        raw = c.stats(stream=False)
        # CPU %
        cpu_delta = raw["cpu_stats"]["cpu_usage"]["total_usage"] - raw["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = raw["cpu_stats"]["system_cpu_usage"] - raw["precpu_stats"]["system_cpu_usage"]
        num_cpus = raw["cpu_stats"].get("online_cpus") or len(raw["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
        cpu_percent = (cpu_delta / system_delta) * num_cpus * 100.0 if system_delta > 0 else 0.0
        # Memory
        mem_usage = raw["memory_stats"].get("usage", 0)
        mem_limit = raw["memory_stats"].get("limit", 1)
        mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0
        return {
            "status": "running",
            "cpu_percent": round(cpu_percent, 2),
            "mem_usage_mb": round(mem_usage / (1024 * 1024), 1),
            "mem_limit_mb": round(mem_limit / (1024 * 1024), 1),
            "mem_percent": round(mem_percent, 2),
        }
    except Exception as e:
        logging.error(f"Error fetching stats for {name}: {e}")
        return {"status": "running", "error": str(e)}


def _sanitize(msg: str) -> str:
    if not msg:
        return ""
    # 1. Truncate to 100 chars to prevent buffer issues
    s = msg[:100]
    # 2. Whitelist only safe characters — removes all shell metacharacters/quotes
    s = _VALID_MSG_CHARS.sub("", s)
    # 3. Strip surrounding whitespace first so a leading space can't shield a
    #    hyphen from lstrip (e.g. " -n hello" -> "-n hello" if stripped after),
    #    then strip leading hyphens to prevent argument injection (e.g. --help,
    #    -n), then strip again for any whitespace the hyphen-strip exposed
    #    (e.g. "- foo" -> " foo" -> "foo").
    s = s.strip().lstrip("-").strip()
    return s


def announce_in_game(name: str, message: str) -> Result:
    if not _check_allowed(name):
        return Result(False, f"container {name} is not allowed")
    try:
        client = _get_client()
        c = _find_container_by_name(client, name)
        if not c:
            return Result(False, f"container {name} not found")
    except docker.errors.APIError as e:
        return Result(False, f"docker error: {e.explanation or e}")
    except _DAEMON_CONNECTION_ERRORS as e:
        logging.error(f"Docker daemon error announcing to {name}: {type(e).__name__}: {e}")
        return Result(False, f"docker daemon error: {type(e).__name__}")

    safe_msg = _sanitize(message)
    if not safe_msg:
        return Result(False, "message is empty after sanitization")
    if "{message}" in CONTAINER_MESSAGE_CMD:
        # Use a literal substring replace, not str.format(): a template with any
        # other brace (e.g. Minecraft's `tellraw @a {"text":"{message}"}`) would
        # make .format() raise KeyError/ValueError. safe_msg can't contain braces
        # (_VALID_MSG_CHARS strips them), so replace is exact and injects nothing.
        cmd = ["/bin/sh", "-c", CONTAINER_MESSAGE_CMD.replace("{message}", safe_msg)]
    else:
        cmd = CONTAINER_MESSAGE_CMD.split() + [safe_msg]

    try:
        res = c.exec_run(cmd)
        out = res.output.decode("utf-8", errors="replace").strip()
        if res.exit_code != 0:
            return Result(False, f"error ({res.exit_code}): {out}")
        return Result(True, f"ok: {out}" if out else "ok")
    except Exception as e:
        return Result(False, f"error: {e}")


async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))
