"""FastAPI HTTP status endpoint for the bot."""

import logging
import os
from collections import deque

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse

from . import docker_control, permissions
from .config import (
    STATUS_TOKEN, STATUS_PORT, ALLOWED_CONTAINERS, LOG_FILE, BOT_TOKEN,
)

app = FastAPI()


async def verify_token(
    x_auth_token: str = Header(None, alias="X-Auth-Token"),
    query_token: str = Query(None, alias="token"),
):
    # If STATUS_TOKEN is empty (user explicitly disabled it), allow access
    if not STATUS_TOKEN:
        return

    token = x_auth_token or query_token
    if not token or token != STATUS_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
def root():
    return RedirectResponse(url="/status")


@app.get("/status", dependencies=[Depends(verify_token)])
def status():
    out = {}
    for name in ALLOWED_CONTAINERS:
        out[name] = docker_control.container_status(name)

    current_perms = permissions.list_permissions()

    recent_logs = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                recent_logs = list(deque(f, maxlen=50))
                recent_logs = [line.strip().replace(BOT_TOKEN, "[REDACTED]") for line in recent_logs]
                if STATUS_TOKEN and STATUS_TOKEN != BOT_TOKEN:
                    recent_logs = [line.replace(STATUS_TOKEN, "[REDACTED]") for line in recent_logs]
        except Exception:
            logging.exception("Error reading logs for status endpoint")
            recent_logs = ["Error reading logs"]

    return {
        "ok": True,
        "containers": out,
        "permissions": current_perms,
        "logs": recent_logs,
    }


def start_api():
    config = uvicorn.Config(app, host="0.0.0.0", port=STATUS_PORT, log_level="warning")
    server = uvicorn.Server(config)
    return server.run()
