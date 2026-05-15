"""Shared helpers for provider title-candidate capture.

These are used by providers to expose title candidates for downstream arXiv ID
recovery. Even though providers call this, the primary consumer is arXiv-only
title recovery, so the utilities live under `discovery.arxiv.recovery`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def extract_title_candidates(
    items: Iterable[Mapping[str, object]],
    *,
    title_key: str = "title",
    max_titles: int | None = None,
) -> list[str]:
    """Extract a best-effort list of title strings from provider result items."""

    titles: list[str] = []
    for item in items:
        raw = item.get(title_key)
        title = _coerce_title(raw)
        if title:
            titles.append(title)
            if max_titles is not None and len(titles) >= max_titles:
                break
    return titles


def _coerce_title(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, list):
        parts = [part.strip() for part in value if isinstance(part, str) and part.strip()]
        if not parts:
            return None
        return " ".join(parts)
    if isinstance(value, Mapping):
        for key in ("value", "title", "text"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return None

