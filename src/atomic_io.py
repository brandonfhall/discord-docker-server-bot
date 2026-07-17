"""Atomic, fsync'd JSON file writes.

Standalone leaf module: stdlib-only, no imports from other project modules.
This is deliberate — `permissions.py`, `history.py`, and `state.py` (for
persisted maintenance mode, L4) all need atomic JSON writes, but they must
not import from each other (that would create domain-crossing dependencies
between the permission store, the audit log, and bot state). Since
`config.py` has import-time side effects (`load_dotenv()` and fail-fast
validation) that make it an inappropriate dependency for these modules, this
gets its own leaf module that each of them can depend on without coupling to
one another.
"""

import json
import os
import tempfile


def atomic_write_json(path: str, data, *, indent: int = 2, mode: int | None = None) -> None:
    """Write `data` as JSON to `path` atomically.

    Writes to a temp file in the same directory as `path` (so the final
    `os.replace` is an atomic rename on the same filesystem), flushing and
    fsyncing the temp file before the replace so the write survives a crash
    right up until the atomic rename itself. The temp file is removed if
    anything fails before the replace completes — no `.tmp` litter on error.

    If `mode` is given, the temp file's permission bits are set to exactly
    `mode` via `os.chmod` (bypassing umask) before the replace, so the final
    file's permissions are deterministic. Without it, the temp file (and
    therefore the replaced file) keeps `tempfile.mkstemp`'s default of
    0o600.
    """
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp")
    success = False
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
        success = True
    finally:
        if not success:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
