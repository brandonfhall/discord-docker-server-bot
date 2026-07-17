import json
import logging
import os
from typing import Dict, List

from .atomic_io import atomic_write_json
from .config import PERMISSIONS_FILE, DEFAULT_ALLOWED_ROLES

# Actions that should always have an entry in the permissions file.
# When new actions are added, include them here so existing installs get backfilled.
# This is the single source of truth — bot.py imports this as VALID_ACTIONS.
ALL_ACTIONS = frozenset(
    {
        "start",
        "stop",
        "stop_now",
        "restart",
        "restart_now",
        "cancel",
        "announce",
        "logs",
        "stats",
        "maintenance",
        "history",
    }
)


def _ensure_file():
    # Ensure directory exists
    directory = os.path.dirname(PERMISSIONS_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)

    if not os.path.exists(PERMISSIONS_FILE):
        logging.info(f"Initializing permissions file at: {os.path.abspath(PERMISSIONS_FILE)}")
        data = {action: list(DEFAULT_ALLOWED_ROLES) for action in sorted(ALL_ACTIONS)}
        with open(PERMISSIONS_FILE, "w", opener=lambda path, flags: __import__("os").open(path, flags, 0o600)) as f:
            json.dump(data, f, indent=2)


_cache = None
_cache_mtime = 0.0


def _load() -> Dict[str, List[str]]:
    global _cache, _cache_mtime
    _ensure_file()

    try:
        current_mtime = os.path.getmtime(PERMISSIONS_FILE)
    except OSError:
        current_mtime = 0.0

    if _cache is not None and current_mtime == _cache_mtime:
        return _cache

    try:
        with open(PERMISSIONS_FILE, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        corrupt_path = PERMISSIONS_FILE + ".corrupt"
        logging.error(
            f"Permissions file {PERMISSIONS_FILE} is corrupted. Restoring defaults; original preserved at {corrupt_path}."
        )
        try:
            os.replace(PERMISSIONS_FILE, corrupt_path)
        except OSError as e:
            logging.warning(f"Could not preserve corrupted permissions file: {e}")
        _ensure_file()
        try:
            with open(PERMISSIONS_FILE, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            # The preserve-and-reinit above didn't actually take -- os.replace
            # failed (e.g. a read-only/permission-broken data/ dir), so the
            # corrupt file is still sitting at PERMISSIONS_FILE and
            # _ensure_file() no-op'd because "the file already exists". Re-reading
            # it just reproduces the same JSONDecodeError. Letting that escape
            # here propagates through is_member_allowed -> has_permission and
            # silently kills every privileged command with no reply to the user
            # (L12) -- so degrade to in-memory defaults instead of raising.
            #
            # Deliberately NOT cached (_cache/_cache_mtime untouched): the file
            # on disk is still corrupt and unfixed, so every subsequent _load()
            # call retries the preserve-and-reload, letting the bot self-heal
            # the moment the underlying filesystem issue is resolved, without
            # requiring a restart. The cost is repeated ERROR logs and a retried
            # os.replace per privileged command while broken -- acceptable for a
            # rare, operator-visible degraded state, and this all runs off the
            # event loop via run_blocking so it doesn't stall the bot.
            logging.error(
                f"Permissions file {PERMISSIONS_FILE} is still unreadable after the recovery attempt "
                "(disk likely read-only or otherwise broken). Falling back to in-memory default "
                "permissions for this call."
            )
            return {action: list(DEFAULT_ALLOWED_ROLES) for action in sorted(ALL_ACTIONS)}

    # Backfill any new actions missing from existing permission files
    missing = [a for a in ALL_ACTIONS if a not in data]
    if missing:
        for action in missing:
            data[action] = list(DEFAULT_ALLOWED_ROLES)
        logging.info(f"Backfilled missing permission actions: {missing}")
        _save(data)

    _cache = data
    _cache_mtime = current_mtime
    return data


def _save(data: Dict[str, List[str]]):
    global _cache, _cache_mtime
    atomic_write_json(PERMISSIONS_FILE, data, indent=2, mode=0o600)
    # Only update the cache once the write is durably on disk — if
    # atomic_write_json raised, we must not claim data that isn't there.
    _cache = data
    try:
        _cache_mtime = os.path.getmtime(PERMISSIONS_FILE)
    except OSError:
        _cache_mtime = 0.0


def is_member_allowed(action: str, member) -> bool:
    data = _load()
    allowed = data.get(action, [])
    member_role_names = [r.name for r in member.roles]
    return any(r in allowed for r in member_role_names)


def add_role(action: str, role_name: str):
    data = _load()
    arr = data.get(action, [])
    if role_name not in arr:
        arr.append(role_name)
    data[action] = arr
    _save(data)


def remove_role(action: str, role_name: str):
    data = _load()
    arr = data.get(action, [])
    if role_name in arr:
        arr.remove(role_name)
    data[action] = arr
    _save(data)


def list_permissions() -> Dict[str, List[str]]:
    return _load()
