import json
import logging
import os
from typing import Dict, List

from .config import PERMISSIONS_FILE, DEFAULT_ALLOWED_ROLES


def _ensure_file():
    # Ensure directory exists
    directory = os.path.dirname(PERMISSIONS_FILE)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)

    if not os.path.exists(PERMISSIONS_FILE):
        logging.info(f"Initializing permissions file at: {os.path.abspath(PERMISSIONS_FILE)}")
        data = {
            "start": DEFAULT_ALLOWED_ROLES,
            "stop": DEFAULT_ALLOWED_ROLES,
            "restart": DEFAULT_ALLOWED_ROLES,
        }
        with open(PERMISSIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)


def _load() -> Dict[str, List[str]]:
    _ensure_file()
    with open(PERMISSIONS_FILE, "r") as f:
        return json.load(f)


def _save(data: Dict[str, List[str]]):
    with open(PERMISSIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


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
