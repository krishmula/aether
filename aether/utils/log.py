"""Logging infrastructure for the pub-sub system.

Provides:
  - setup_logging(): Configure handlers, formatters, and async dispatch.
    Call once at each CLI entry point before anything else.
  - BoundLogger: LoggerAdapter subclass for binding per-component context
    (broker address, node address, etc.) into every log record automatically.
  - bind_msg_id() / reset_msg_id(): ContextVar helpers for propagating a
    gossip message correlation ID through the entire call stack so every log
    record emitted during gossip dispatch carries msg_id without being passed
    explicitly to every function.
  - log_header() / log_separator(): Terminal UI helpers for interactive CLIs.
    These write directly to stdout and are NOT log records.
"""

from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import sys
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from queue import Queue
from typing import Any, MutableMapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Correlation ID — propagates gossip msg_id through the call stack
# ---------------------------------------------------------------------------

_msg_id_ctx: ContextVar[Optional[str]] = ContextVar("msg_id", default=None)


def bind_msg_id(msg_id: str) -> Token:
    """Bind a gossip message ID to the current execution context.

    Returns a reset token that must be passed to reset_msg_id() in a
    finally block to avoid leaking context across unrelated messages.
    """
    return _msg_id_ctx.set(msg_id)


def reset_msg_id(token: Token) -> None:
    """Remove the gossip message ID from the current execution context."""
    _msg_id_ctx.reset(token)


# ---------------------------------------------------------------------------
# BoundLogger — LoggerAdapter with automatic context injection
# ---------------------------------------------------------------------------


class BoundLogger(logging.LoggerAdapter):
    """LoggerAdapter that merges bound fields and the active correlation ID
    into every log record's extra dict.

    Instantiate once per component, binding its identifying fields:

        logger = logging.getLogger(__name__)
        self.log = BoundLogger(logger, {"broker": str(self.address)})

    Every subsequent call (self.log.info / self.log.debug / etc.) will
    automatically include broker=<addr> — and msg_id=<uuid> whenever
    bind_msg_id() is active on the current thread's context.
    """

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> Tuple[str, MutableMapping[str, Any]]:
        extra: dict[str, Any] = dict(self.extra) if self.extra else {}
        extra.update(kwargs.get("extra") or {})

        msg_id = _msg_id_ctx.get()
        if msg_id is not None:
            extra["msg_id"] = msg_id

        kwargs["extra"] = extra
        return msg, kwargs


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

_LEVEL_STYLES: dict[str, tuple[str, str]] = {
    "DEBUG": ("\033[90m", "◆"),  # dim/bright-black
    "INFO": ("\033[36m", "ℹ"),  # cyan
    "WARNING": ("\033[33m", "⚠"),  # yellow
    "ERROR": ("\033[31m", "✗"),  # red
    "CRITICAL": ("\033[91m", "✗✗"),  # bright red
}

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[90m"
_CYAN_BOLD = "\033[1m\033[96m"
_WHITE_BOLD = "\033[1m\033[97m"

# Fields that BoundLogger or bind_msg_id may attach to records.
_CONTEXT_FIELDS = ("broker", "node", "publisher", "subscriber", "bootstrap", "msg_id")


class ColoredFormatter(logging.Formatter):
    """Human-readable, colored formatter for interactive terminal sessions.

    Output format:
        [HH:MM:SS.mmm] ℹ gossip.broker │ message  [broker=127.0.0.1:5001, msg_id=abc123]

    The logger name has the leading "aether." stripped for brevity.
    Context fields are appended in dim brackets when present.
    Exception tracebacks are appended on subsequent lines.
    """

    def format(self, record: logging.LogRecord) -> str:
        color, symbol = _LEVEL_STYLES.get(record.levelname, (_RESET, "?"))

        # Local time, millisecond precision — friendly for interactive use.
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]

        # Strip package prefix: aether.gossip.broker → gossip.broker
        name = record.name.removeprefix("aether.")

        context_parts = [
            f"{field}={getattr(record, field)}"
            for field in _CONTEXT_FIELDS
            if getattr(record, field, None) is not None
        ]
        context_str = (
            f"  {_DIM}[{', '.join(context_parts)}]{_RESET}" if context_parts else ""
        )

        exc_str = (
            "\n" + self.formatException(record.exc_info) if record.exc_info else ""
        )

        return (
            f"{_DIM}[{ts}]{_RESET} "
            f"{color}{symbol} {_BOLD}{name}{_RESET} "
            f"{color}│{_RESET} "
            f"{record.getMessage()}"
            f"{context_str}"
            f"{exc_str}"
        )


class JSONFormatter(logging.Formatter):
    """Machine-readable JSON formatter for production deployments.

    Each log line is a single JSON object suitable for ingestion by
    Datadog, Splunk, ELK, or any structured log aggregator.

    Fields always present:
        timestamp  ISO-8601 UTC with microseconds
        level      lowercased level name (debug / info / warning / error / critical)
        logger     fully-qualified logger name (aether.gossip.broker)
        thread     thread name (set by threading.Thread(name=...))
        message    formatted log message

    Fields present when set by BoundLogger or bind_msg_id():
        broker / node / publisher / subscriber / bootstrap
        msg_id
    """

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "thread": record.threadName,
            "message": record.getMessage(),
        }

        for field in _CONTEXT_FIELDS:
            val = getattr(record, field, None)
            if val is not None:
                obj[field] = val

        if record.exc_info:
            obj["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(obj, default=str)


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

_listener: Optional[logging.handlers.QueueListener] = None


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_console: bool = False,
) -> None:
    """Configure the aether logger hierarchy. Call once per process.

    Must be called at the top of each CLI entry point's main(), before any
    library code runs, so that the first log records are captured correctly.

    All log calls are non-blocking: a QueueHandler enqueues records
    instantly on the calling thread; a dedicated QueueListener thread
    drains the queue and performs actual I/O (file writes, stdout writes).

    Args:
        level:        Minimum level for the console handler.
                      One of DEBUG / INFO / WARNING / ERROR. Default: INFO.
        log_file:     Optional path to a rotating JSON log file.
                      When set, all records at DEBUG+ are written as JSON,
                      independent of the console level.
        json_console: Emit JSON on stdout even when attached to a TTY.
                      Set to True in containerised / systemd deployments
                      where stdout is collected by a log aggregator.
    """
    global _listener

    root = logging.getLogger("aether")

    # Idempotent: if already configured, skip (handles test re-use).
    if root.handlers:
        return

    # Root aether logger passes everything; handlers filter by level.
    root.setLevel(logging.DEBUG)

    log_queue: Queue = Queue(maxsize=-1)
    handlers: list[logging.Handler] = []

    # -- Console handler ---------------------------------------------------
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    use_json = json_console or not sys.stdout.isatty()
    console.setFormatter(JSONFormatter() if use_json else ColoredFormatter())
    handlers.append(console)

    # -- Rotating JSON file handler (optional) ----------------------------
    if log_file:
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=50 * 1024 * 1024,  # 50 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JSONFormatter())
        handlers.append(fh)

    # -- Async dispatch via QueueListener ---------------------------------
    # Log calls on daemon threads are non-blocking: they enqueue a record
    # and return immediately. The listener drains on its own thread.
    _listener = logging.handlers.QueueListener(
        log_queue, *handlers, respect_handler_level=True
    )
    _listener.start()
    atexit.register(_listener.stop)

    queue_handler = logging.handlers.QueueHandler(log_queue)
    queue_handler.setLevel(logging.DEBUG)
    root.addHandler(queue_handler)


# ---------------------------------------------------------------------------
# Terminal UI helpers — direct stdout writes, not log records
# ---------------------------------------------------------------------------


def log_header(title: str) -> None:
    """Print a decorated header box to stdout. Interactive CLI use only."""
    print(f"\n{_CYAN_BOLD}╔{'═' * 58}╗{_RESET}")
    print(
        f"{_CYAN_BOLD}║{_RESET} "
        f"{_WHITE_BOLD}{title.center(56)}{_RESET} "
        f"{_CYAN_BOLD}║{_RESET}"
    )
    print(f"{_CYAN_BOLD}╚{'═' * 58}╝{_RESET}\n")


def log_separator(title: Optional[str] = None) -> None:
    """Print a visual separator to stdout. Interactive CLI use only."""
    if title:
        print(f"\n{_CYAN_BOLD}{'─' * 20} {title} {'─' * 20}{_RESET}")
    else:
        print(f"{_DIM}{'─' * 60}{_RESET}")
