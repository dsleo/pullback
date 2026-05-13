"""Lightweight async hook registry for instrumentation and observers."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from .logging import get_logger

HookHandler = Callable[..., Awaitable[None] | None]


class HookRegistry:
    def __init__(
        self,
        *,
        allowed_events: Iterable[str] | None = None,
        name: str = "hooks",
        raise_exceptions: bool = False,
    ) -> None:
        self._allowed = set(allowed_events or [])
        self._strict = allowed_events is not None
        self._raise = raise_exceptions
        self._hooks: dict[str, list[HookHandler]] = {event: [] for event in self._allowed}
        self._log = get_logger(name)

    def on(self, event: str, handler: HookHandler) -> None:
        if self._strict and event not in self._allowed:
            raise ValueError(f"Unknown hook event: {event}")
        self._hooks.setdefault(event, []).append(handler)

    async def emit(self, event: str, **kwargs: Any) -> None:
        handlers = self._hooks.get(event, [])
        if not handlers:
            return
        for handler in handlers:
            try:
                result = handler(**kwargs)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                if self._raise:
                    raise
                self._log.warning(
                    "hook.failed event={} error_type={} error_repr={}",
                    event,
                    type(exc).__name__,
                    repr(exc),
                )
