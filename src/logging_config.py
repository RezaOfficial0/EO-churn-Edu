"""Central logging configuration for the whole project.

Rule this module enforces:

* Library modules (``src/``, ``pipeline/``, ``api/``) only ever call
  ``logging.getLogger(__name__)``. They never attach handlers themselves --
  otherwise merely importing them would hijack the logging of whatever
  application embeds this package.
* Configuration happens exactly once, here, and it is invoked from the
  entry points (``running_train_pipeline.py``, ``api/main.py``).

H2 FIX -- why this file exists at all:
Before this, the project had no ``logging.basicConfig`` anywhere. Every
``logger.info()`` / ``logger.warning()`` call in the codebase -- including the
entire data validation layer -- was discarded: the root logger sits at WARNING
with no handler, so INFO records were dropped outright and WARNING records went
to the "handler of last resort". In practice validation ran on every pipeline
execution and reported to nobody.
"""

import logging
import os

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str | None = None) -> str:
    """Configure root logging for this process. Safe to call more than once.

    Args:
        level: explicit level name ("DEBUG", "INFO", "WARNING", ...). When
            omitted, the ``LOG_LEVEL`` environment variable is used (config.py
            already calls ``load_dotenv()``, so ``.env`` works too), and the
            final fallback is INFO.

    Returns:
        The level name that was actually applied -- handy in tests and for a
        startup log line.
    """
    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()

    logging.basicConfig(
        level=resolved,
        format=_DEFAULT_FORMAT,
        datefmt=_DEFAULT_DATEFMT,
        # force=True replaces any root handler already installed by another
        # framework (uvicorn, pytest, a notebook kernel) so our format wins and
        # a record is never emitted twice. uvicorn's own "uvicorn.access" /
        # "uvicorn.error" loggers carry their own handlers with propagate=False,
        # so its request log is unaffected by this.
        force=True,
    )
    return resolved
