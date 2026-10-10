"""Logging to stderr plus one file per process (Claude Desktop runs several server instances at once).

stdout is the MCP protocol channel, so nothing here ever writes to it.
"""

import logging
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

LOG_PREFIX = "research-mcp"
KEEP_DAYS = 14
# Library loggers that would otherwise write full URLs (with query strings) at INFO.
QUIET_LOGGERS = ("httpx2", "httpcore2", "trafilatura", "htmldate", "courlan", "charset_normalizer", "primp", "ddgs")
_FORMAT = "%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


class RedactingFormatter(logging.Formatter):
    """Rewrites every URL in the final line (message and traceback) to host + path, whoever logged it:
    trafilatura, for one, logs full URLs at WARNING."""

    def format(self, record: logging.LogRecord) -> str:
        return _URL.sub(lambda match: redact_url(match.group(0)), super().format(record))


def setup_logging(log_dir: Path, *, level: int = logging.INFO, now: Callable[[], float] = time.time) -> Path | None:
    """Install our handlers on the root logger and return the log file path (None when the folder is unwritable).

    Call it before building the MCPServer: the SDK's logging.basicConfig is a no-op once root has handlers.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "research_mcp", False):
            root.removeHandler(handler)
            handler.close()

    formatter = RedactingFormatter(_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    path: Path | None = None
    problem: str | None = None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _prune(log_dir, now())
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))
        path = log_dir / f"{LOG_PREFIX}-{stamp}-{os.getpid()}.log"
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    except OSError as exc:
        path = None
        problem = f"log folder {log_dir} is not writable ({exc!r}); logging to stderr only"

    for handler in handlers:
        handler.research_mcp = True  # type: ignore[attr-defined]
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if problem:
        logging.getLogger("research_mcp").warning(problem)
    return path


def _prune(log_dir: Path, now: float) -> None:
    cutoff = now - KEEP_DAYS * 24 * 3600
    for old in log_dir.glob(f"{LOG_PREFIX}-*.log"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:  # another instance may still hold it open on Windows
            pass


def redact_url(url: str) -> str:
    """Host and path only: query strings and fragments can carry personal data."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid url>"
    if not parts.hostname:
        return "<invalid url>"
    return f"{parts.hostname}{parts.path or '/'}"
