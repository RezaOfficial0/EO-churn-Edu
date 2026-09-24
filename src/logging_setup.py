"""One place that turns logging on.

Without this, every `logger.info(...)` / `logger.warning(...)` in the codebase
goes nowhere (the root logger defaults to WARNING and has no handler), so the
validation layer would run and report to no one. Each entry point
(`running_train_pipeline.py`, the API, the daily pipeline) calls
`configure_logging()` once at startup.

Every line also carries a request id. The API's access log used to be tied to a
traceback by the request path, and that path contained the student id
(`GET /predict/STU300001`) - so correlation and privacy were in direct conflict. An
opaque per-request id gives the correlation without the identifier.
"""
import contextvars
import logging
import uuid

_ALREADY_CONFIGURED = False

# Set by the API's access-log middleware for the duration of one request. "-" for
# anything that is not serving a request (training runs, the daily pipeline).
request_id_var = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    """Put `request_id` on every record, so the format string can always use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def new_request_id() -> str:
    """A short opaque id for one request. Not a secret, and not derived from the data."""
    return uuid.uuid4().hex[:12]


def configure_logging(level: int = logging.INFO) -> None:
    """Attach a console handler to the root logger. Safe to call more than once."""
    global _ALREADY_CONFIGURED
    if _ALREADY_CONFIGURED:
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # On the handler, not on a logger: a filter on a logger does not apply to records
    # propagated from its children, and the format string needs the attribute on
    # every record that reaches the handler.
    for handler in logging.getLogger().handlers:
        handler.addFilter(_RequestIdFilter())
    _ALREADY_CONFIGURED = True
