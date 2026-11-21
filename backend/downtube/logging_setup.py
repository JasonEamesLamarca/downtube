"""Logging with rotation and secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

from downtube.config import Config

_REDACT: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"((?:access_token|refresh_token|id_token|client_secret|code_verifier|(?<![A-Za-z_])code)=)[^&\s\"']+", re.I
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(\"(?:access_token|refresh_token|id_token|client_secret)\"\s*:\s*\")[^\"]+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+"), r"\1[REDACTED]"),
    (re.compile(r"\bya29\.[A-Za-z0-9._\-]+"), "[REDACTED]"),
    (re.compile(r"\b1//[A-Za-z0-9._\-]{20,}"), "[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, repl in _REDACT:
        text = pat.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        red = redact(msg)
        if red != msg:
            record.msg, record.args = red, None
        return True


LOG_FILE = "downtube.log"


def setup_logging(cfg: Config) -> None:
    root = logging.getLogger()
    if getattr(root, "_downtube_configured", False):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(cfg.log_dir / LOG_FILE, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
        )
    except OSError:
        pass
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactingFilter())
        root.addHandler(h)
    root.setLevel(cfg.log_level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    root._downtube_configured = True  # type: ignore[attr-defined]
