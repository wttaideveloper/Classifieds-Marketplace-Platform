"""Background jobs of the monolith, each started by exactly one v2 service (the owner of its data).

``job("module:function")`` returns a start hook; DISABLE_BACKGROUND_JOBS=true keeps it from starting (tests,
debugging). The job's own switches (TRAINING_REMINDERS_ENABLED, SERVICE_BOOKING_REMINDERS_ENABLED, ...) still apply.
"""
import importlib
import logging
import os

logger = logging.getLogger(__name__)


def job(target: str):
    def start():
        if os.environ.get("DISABLE_BACKGROUND_JOBS", "").lower() in ("1", "true", "yes"):
            logger.info("background job %s not started (DISABLE_BACKGROUND_JOBS)", target)
            return
        module, func = target.split(":")
        getattr(importlib.import_module(module), func)()
        logger.info("background job started: %s", target)

    start.__name__ = f"start_{target.split(':')[0].rsplit('.', 1)[-1]}"
    return start
