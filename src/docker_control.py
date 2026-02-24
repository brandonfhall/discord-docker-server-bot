import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import docker

from .config import ALLOWED_CONTAINERS, IN_GAME_ANNOUNCE_CMD

_executor = ThreadPoolExecutor(max_workers=3)


def _get_client():
    return docker.from_env()


def _find_container_by_name(client, name: str):
    # match by exact name only (avoid substring matches)
    try:
        containers = client.containers.list(all=True)
        for c in containers:
            if c.name == name:
                return c
    except Exception:
        return None
    return None


def _check_allowed(name: str) -> bool:
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
    client = _get_client()
    c = _find_container_by_name(client, name)
    if not c:
        return None
    # refresh
    c.reload()
    return c.status


def announce_in_game(name: str, message: str) -> str:
    if not _check_allowed(name):
        return f"container {name} is not allowed"
    client = _get_client()
    c = _find_container_by_name(client, name)
    if not c:
        return f"container {name} not found"
    # Sanitize and limit message to reduce injection risks
    def _sanitize(msg: str) -> str:
        if not msg:
            return ""
        s = msg.replace("\n", " ").replace("\r", " ")
        # trim excessive length
        if len(s) > 200:
            s = s[:200]
        # remove shell metacharacters
        for ch in [';', '&', '|', '$', '`', '>', '<', '\\']:
            s = s.replace(ch, '')
        return s

    safe_msg = _sanitize(message)
    # Prefer exec_run with argument list (no shell) to avoid shell interpolation
    # The IN_GAME_ANNOUNCE_CMD should be a template that results in an argv-style command
    # If it contains spaces and is intended to be a single shell string, we run via /bin/sh -c
    if "{message}" in IN_GAME_ANNOUNCE_CMD:
        cmd = IN_GAME_ANNOUNCE_CMD.format(message=safe_msg)
        try:
            res = c.exec_run(["/bin/sh", "-c", cmd])
            return f"ok: {res.exit_code}"
        except Exception as e:
            return f"error: {e}"
    else:
        # attempt to split into args; user-provided template should be adjusted to avoid this path
        try:
            argv = IN_GAME_ANNOUNCE_CMD.split() + [safe_msg]
            res = c.exec_run(argv)
            return f"ok: {res.exit_code}"
        except Exception as e:
            return f"error: {e}"


async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))
