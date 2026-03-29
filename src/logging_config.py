"""Logging setup with token redaction filter."""

import logging
import os
from logging.handlers import RotatingFileHandler


class RedactingFilter(logging.Filter):
    """Strips sensitive token values from log records before they reach any handler."""

    def __init__(self, tokens):
        super().__init__()
        self._tokens = [t for t in tokens if t]

    def filter(self, record):
        if self._tokens:
            msg = record.getMessage()
            for token in self._tokens:
                msg = msg.replace(token, "[REDACTED]")
            record.msg = msg
            record.args = ()
        return True


def setup_logging(log_file: str, log_level: str, tokens: list):
    """Configure root logger with console + rotating file handler and token redaction."""
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=1),
        ],
    )

    redact_filter = RedactingFilter(tokens)
    for handler in logging.root.handlers:
        handler.addFilter(redact_filter)
