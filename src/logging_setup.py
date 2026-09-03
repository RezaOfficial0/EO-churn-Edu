"""One place that turns logging on.

Without this, every `logger.info(...)` / `logger.warning(...)` in the codebase
goes nowhere (the root logger defaults to WARNING and has no handler), so the
validation layer would run and report to no one. Each entry point
(`running_train_pipeline.py`, the API, the daily pipeline) calls
`configure_logging()` once at startup.
"""
import logging

_ALREADY_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Attach a console handler to the root logger. Safe to call more than once."""
    global _ALREADY_CONFIGURED
    if _ALREADY_CONFIGURED:
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _ALREADY_CONFIGURED = True
