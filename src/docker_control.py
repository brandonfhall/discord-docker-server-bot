import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import docker

from .config import ALLOWED_CONTAINERS, CONTAINER_MESSAGE_CMD, DOCKER_MAX_WORKERS

_executor = ThreadPoolExecutor(max_workers=DOCKER_MAX_WORKERS)

# Validate container names: alphanumeric, underscore, dot, hyphen only
_VALID_CONTAINER_NAME = re.compile(r'^[a-zA-Z0-9_.-]+$')
# Validate messages: Allow alphanumeric, spaces, and basic punctuation only
_VALID_MSG_CHARS = re.compile(r'[^a-zA-Z0-9 .,!?:_\-]')


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
    except Exception:
        return None


def _check_allowed(name: str) -> bool:
    if not _validate_container_name(name):
        return False
    if not ALLOWED_CONTAINERS:
        return False
    return name in ALLOWED_CONTAINERS


def start_container(name: str) -> str:
    if not _check_allowed(name):
        return f"container {name} is not allowed"
    client = _get_client()
    c = _find_container_by_name(client, name)
    if not c:
        return f"container {name} not found"
    c.reload()
    if c.status == "running":
        return "already running"
    c.start()
    return "started"


def stop_container(name: str, timeout: int = 10) -> str:
    if not _check_allowed(name):
        return f"container {name} is not allowed"
    client = _get_client()
    c = _find_container_by_name(client, name)
    if not c:
        return f"container {name} not found"
    c.reload()
    if c.status != "running":
        return "not running"
    c.stop(timeout=timeout)
    return "stopped"


def restart_container(name: str, timeout: int = 10) -> str:
    if not _check_allowed(name):
        return f"container {name} is not allowed"
    client = _get_client()
    c = _find_container_by_name(client, name)
    if not c:
        return f"container {name} not found"
    c.restart(timeout=timeout)
    return "restarted"


def container_status(name: str) -> Optional[str]:
    if not _check_allowed(name):
        return None
    client = _get_client()
    c = _find_container_by_name(client, name)
    if not c:
        return None
    # refresh
    c.reload()
    return c.status


def _sanitize(msg: str) -> str:
    if not msg:
        return ""
    # Strict security hardening:
    # 1. Truncate to 100 chars to prevent buffer issues
    s = msg[:100]
    # 2. Whitelist only safe characters. Removes all shell metacharacters/quotes.
    s = _VALID_MSG_CHARS.sub('', s)
    return s.strip()


def announce_in_game(name: str, message: str) -> str:
    if not _check_allowed(name):
        return f"container {name} is not allowed"
    client = _get_client()
    c = _find_container_by_name(client, name)
    if not c:
        return f"container {name} not found"

    safe_msg = _sanitize(message)
    # Prefer exec_run with argument list (no shell) to avoid shell interpolation
    # The CONTAINER_MESSAGE_CMD should be a template that results in an argv-style command
    # If it contains spaces and is intended to be a single shell string, we run via /bin/sh -c
    if "{message}" in CONTAINER_MESSAGE_CMD:
        cmd = CONTAINER_MESSAGE_CMD.format(message=safe_msg)
        try:
            res = c.exec_run(["/bin/sh", "-c", cmd])
            out = res.output.decode('utf-8').strip()
            if res.exit_code != 0:
                return f"error ({res.exit_code}): {out}"
            return f"ok: {out}" if out else "ok"
        except Exception as e:
            return f"error: {e}"
    else:
        # attempt to split into args; user-provided template should be adjusted to avoid this path
        try:
            argv = CONTAINER_MESSAGE_CMD.split() + [safe_msg]
            res = c.exec_run(argv)
            out = res.output.decode('utf-8').strip()
            if res.exit_code != 0:
                return f"error ({res.exit_code}): {out}"
            return f"ok: {out}" if out else "ok"
        except Exception as e:
            return f"error: {e}"


async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))
