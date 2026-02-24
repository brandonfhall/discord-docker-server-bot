import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import docker

from .config import ALLOWED_CONTAINERS, CONTAINER_MESSAGE_CMD

_executor = ThreadPoolExecutor(max_workers=3)

# Validate container names: alphanumeric, underscore, dot, hyphen only
_VALID_CONTAINER_NAME = re.compile(r'^[a-zA-Z0-9_.-]+$')


def _validate_container_name(name: str) -> bool:
    """Check if container name is valid (alphanumeric, underscore, dot, hyphen)."""
    if not name or not isinstance(name, str) or len(name) > 255:
        return False
    return _VALID_CONTAINER_NAME.match(name) is not None


def _get_client():
    return docker.from_env()


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
    s = msg.replace("\n", " ").replace("\r", " ")
    # trim excessive length
    if len(s) > 200:
        s = s[:200]
    # remove shell metacharacters and quotes to prevent injection
    for ch in [';', '&', '|', '$', '`', '>', '<', '\\', '(', ')', '"', "'"]:
        s = s.replace(ch, '')
    return s


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
