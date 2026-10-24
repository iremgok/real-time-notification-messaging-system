"""Central logging configuration with redaction of secrets and PII.

Every handler (ours and uvicorn's) gets a ``RedactingFilter`` so that raw JWTs,
``?token=`` query params, passwords and e-mail addresses never reach the logs,
even if a developer accidentally logs them.
"""

import logging
import re
import sys

from src.core.config import settings

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # Credentials inside connection URLs: postgresql+asyncpg://user:secret@host -> user:[REDACTED]@host
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^:/@\s]*:)[^@\s]+@"), r"\1[REDACTED]@"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([?&](?:token|access_token)=)[^&\s\"']+"), r"\1[REDACTED]"),
    (
        re.compile(r"(?i)([\"']?(?:password|passwd|secret|token|access_token)[\"']?\s*[:=]\s*[\"']?)[^\"',\s}&]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
]

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrites the fully formatted message so arguments are redacted as well."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed record; let the handler report it
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def setup_logging() -> None:
    redacting_filter = RedactingFilter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(redacting_filter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL.upper())

    # uvicorn installs its own handlers before importing the app; protect them too.
    for name in _UVICORN_LOGGERS:
        for uvicorn_handler in logging.getLogger(name).handlers:
            uvicorn_handler.addFilter(redacting_filter)

    # SQL echo would print bound parameters (e-mails, hashes); keep it quiet.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"chat.{name}")
