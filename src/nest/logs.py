"""Logging: rotating file under data/logs (10MB × 5), plus stderr when running in the foreground, plus
the console's live log (LogBuffer: the newest records in memory, each also sent as an SSE "log" event)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.handlers
import traceback
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from nest.llm.http import scrub

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
SOURCES = ("nest", "uvicorn.error")  # loggers the console shows (and everything below them)
BUFFER_SIZE = 1000
MESSAGE_LIMIT = 2000
SECRET_MIN = 8  # shorter values (a port, a log level) are not secrets and would mangle ordinary text


def configure(log_dir: Path, level: str, *, console: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            log_dir / "nest.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
    ]
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=level.upper(), format=FORMAT, handlers=handlers, force=True)
    # httpx logs every request at INFO; with several accounts that drowns the useful lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)


class LogBuffer(logging.Handler):
    """The newest INFO-and-above records as {id, at, level, logger, message}, scrubbed of keys.

    Records arrive from any thread (worker threads, the database thread); each is handed to `publish`
    on `loop`'s thread, since the broadcaster's queues are not thread-safe.
    """

    def __init__(
        self,
        publish: Callable[[dict[str, Any]], None],
        loop: asyncio.AbstractEventLoop,
        *,
        secrets: Callable[[], Iterable[str]] = tuple,
        size: int = BUFFER_SIZE,
    ) -> None:
        super().__init__(logging.INFO)
        self._publish = publish
        self._loop = loop
        self._secrets = secrets
        self._records: deque[tuple[int, dict[str, Any]]] = deque(maxlen=size)
        self._next_id = 1

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                text += "\n" + "".join(traceback.format_exception_only(record.exc_info[1])).strip()
            secrets = sorted((s for s in self._secrets() if len(s) >= SECRET_MIN), key=len, reverse=True)
            item = {
                "id": self._next_id,
                "at": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": scrub(text, *secrets)[:MESSAGE_LIMIT],
            }
            self._next_id += 1
            self._records.append((record.levelno, item))
            self._send(item)
        except Exception:
            self.handleError(record)

    def _send(self, item: dict[str, Any]) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._publish(item)
        else:
            with contextlib.suppress(RuntimeError):  # the loop has closed: nobody is listening
                self._loop.call_soon_threadsafe(self._publish, item)

    def records(self, *, level: int = logging.INFO, limit: int = 200) -> list[dict[str, Any]]:
        """The newest `limit` records at or above `level`, newest last."""
        with self.lock or contextlib.nullcontext():
            matching = [item for levelno, item in self._records if levelno >= level]
        return matching[-limit:] if limit > 0 else []


@contextlib.contextmanager
def attached(handler: logging.Handler, sources: Iterable[str] = SOURCES) -> Iterator[logging.Handler]:
    """Add `handler` to the source loggers for the duration (an app's lifespan); always removed after."""
    loggers = [logging.getLogger(name) for name in sources]
    for logger in loggers:
        logger.addHandler(handler)
    try:
        yield handler
    finally:
        for logger in loggers:
            logger.removeHandler(handler)
        handler.close()
