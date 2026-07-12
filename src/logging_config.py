"""Logging setup with token redaction filter."""

import logging
import os
import traceback
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

            # exc_info is formatted separately from the message and would otherwise
            # reach handlers un-redacted if a token appears in an exception's str().
            if record.exc_info and not record.exc_text:
                text = "".join(traceback.format_exception(*record.exc_info))
                for token in self._tokens:
                    text = text.replace(token, "[REDACTED]")
                record.exc_text = text
                record.exc_info = None
        return True


def setup_logging(log_file: str, log_level: str, tokens: list):
    """Configure root logger with console + rotating file handler and token redaction."""
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

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
