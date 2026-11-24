"""Run a Socket.IO coroutine from synchronous code.

REST routes declared with plain ``def`` run in FastAPI's worker threadpool, and our background
notification jobs run on daemon threads. Neither has an asyncio event loop, so the old
``asyncio.get_event_loop()`` pattern raised ``RuntimeError: There is no current event loop`` there
(Python 3.12) — and the realtime emit that was supposed to follow every saved notification was
silently dropped (the feed row still existed, the Socket.IO ``notification`` event never fired).

The server's own loop is captured at startup; a coroutine started from another thread is handed to
it with ``run_coroutine_threadsafe``. That matters beyond tidiness: the Socket.IO server (and its
Redis pub/sub manager in a split deployment) is bound to that loop.
"""
import asyncio
import logging
from concurrent.futures import Future

logger = logging.getLogger(__name__)

_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _main_loop
    _main_loop = loop


def _log_failure(future: Future) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("realtime emit failed")


def run_coroutine(coro) -> None:
    """Fire-and-forget: schedule ``coro`` wherever it can actually run. Never raises."""
    try:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is not None:  # called from async code on the server loop
            running.create_task(coro)
        elif _main_loop is not None and _main_loop.is_running():  # worker / daemon thread
            _log_failure_later = asyncio.run_coroutine_threadsafe(coro, _main_loop)
            _log_failure_later.add_done_callback(_log_failure)
        else:  # no server loop (scripts, tests): run it to completion right here
            asyncio.run(coro)
    except Exception:
        coro.close()
        logger.exception("could not schedule realtime emit")


def has_main_loop() -> bool:
    return _main_loop is not None and _main_loop.is_running()
