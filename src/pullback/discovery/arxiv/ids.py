"""Helpers for extracting, normalizing, and deduplicating arXiv IDs from provider payloads."""

from __future__ import annotations

import re

ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|e-print|src)/(?P<id>[^?#]+)", re.IGNORECASE)
ARXIV_PREFIX_RE = re.compile(
    r"arxiv:\s*(?P<id>(?:\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?)",
    re.IGNORECASE,
)
ARXIV_VERSION_RE = re.compile(r"^(?P<id>.+?)v\d+$", re.IGNORECASE)
ARXIV_ID_RE = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?", re.IGNORECASE)


def normalize_arxiv_id(value: str) -> str:
    raw = value.strip().removeprefix("arXiv:").removesuffix(".pdf").strip("/")
    match = ARXIV_VERSION_RE.match(raw)
    if match:
        return match.group("id")
    return raw


def looks_like_arxiv_id(value: str) -> bool:
    return bool(ARXIV_ID_RE.fullmatch(value.strip()))


def extract_arxiv_id_from_text(value: str | None, *, allow_bare: bool = False) -> str | None:
    if not value:
        return None
    candidate = value.strip()

    if allow_bare and looks_like_arxiv_id(candidate):
        return normalize_arxiv_id(candidate)

    m = ARXIV_URL_RE.search(candidate)
    if m:
        parsed = normalize_arxiv_id(m.group("id"))
        return parsed if looks_like_arxiv_id(parsed) else None

    m2 = ARXIV_PREFIX_RE.search(candidate)
    if m2:
        parsed = normalize_arxiv_id(m2.group("id"))
        return parsed if looks_like_arxiv_id(parsed) else None

    return None


def dedupe_preserve(ids: list[str], *, max_results: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in ids:
        normalized = normalize_arxiv_id(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
        if len(out) >= max_results:
            break
    return out


__all__ = [
    "normalize_arxiv_id",
    "looks_like_arxiv_id",
    "extract_arxiv_id_from_text",
    "dedupe_preserve",
]
