"""Logging/tracing setup with request context and optional Logfire instrumentation."""

from __future__ import annotations

import contextvars
from contextlib import nullcontext
import os
from pathlib import Path
import sys
from typing import Protocol, cast

from loguru import logger

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_is_configured = False


class _LogfireLike(Protocol):
    def span(self, name: str, **fields: object): ...

    def info(self, message: str, **fields: object) -> None: ...


_logfire: _LogfireLike | None = None


def _inject_context(record: dict[str, object]) -> None:
    extra = cast(dict[str, object], record.get("extra", {}))
    extra.setdefault("request_id", _request_id_ctx.get())
    extra.setdefault("component", "-")
    record["extra"] = extra


def _setup_loguru() -> None:
    global _is_configured
    if _is_configured:
        return

    level = os.getenv("MATHGENT_LOG_LEVEL", "INFO").upper()
    serialize = os.getenv("MATHGENT_LOG_JSON", "0").lower() in {"1", "true", "yes", "on"}

    logger.remove()
    logger.configure(extra={"request_id": "-", "component": "-"})
    fmt = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
        "req={extra[request_id]} | {extra[component]} | {message}"
    )
    logger.add(
        sys.stdout,
        level=level,
        serialize=serialize,
        backtrace=False,
        diagnose=False,
        format=fmt,
    )

    file_enabled = os.getenv("MATHGENT_LOG_FILE_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
    if file_enabled:
        log_file = Path(os.getenv("MATHGENT_LOG_FILE", "logs/mathgent.log"))
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_file),
            level=level,
            serialize=serialize,
            backtrace=False,
            diagnose=False,
            rotation=os.getenv("MATHGENT_LOG_FILE_ROTATION", "20 MB"),
            retention=os.getenv("MATHGENT_LOG_FILE_RETENTION", "14 days"),
            enqueue=True,
            format=fmt,
        )

    _is_configured = True


def _setup_logfire_if_available() -> None:
    global _logfire
    enabled = os.getenv("MATHGENT_ENABLE_LOGFIRE", "1").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return

    try:
        import logfire as lf  # type: ignore
    except ImportError:
        logger.bind(component="observability").info("logfire.not_installed using_loguru_only=true")
        return

    send_to_logfire = os.getenv("MATHGENT_LOGFIRE_SEND", "0").lower() in {"1", "true", "yes", "on"}
    service_name = os.getenv("MATHGENT_SERVICE_NAME", "mathgent")
    try:
        lf.configure(service_name=service_name, send_to_logfire=send_to_logfire)
        _logfire = lf
        logger.bind(component="observability").info(
            "logfire.enabled service_name={} send_to_logfire={}", service_name, send_to_logfire
        )
    except Exception as exc:  # pragma: no cover
        logger.bind(component="observability").error("logfire.configure_failed error={}", exc)


def setup_observability() -> None:
    _setup_loguru()
    _setup_logfire_if_available()


def get_logger(component: str):
    return logger.patch(_inject_context).bind(component=component)


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return _request_id_ctx.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id_ctx.reset(token)


def get_agent_instrumentation() -> bool:
    return os.getenv("MATHGENT_PYDANTICAI_INSTRUMENT", "1").lower() in {"1", "true", "yes", "on"}


def trace_span(name: str, **fields: object):
    if _logfire is not None and hasattr(_logfire, "span"):
        return _logfire.span(name, **fields)
    return nullcontext()


def logfire_info(message: str, **fields: object) -> None:
    if _logfire is not None and hasattr(_logfire, "info"):
        _logfire.info(message, **fields)
