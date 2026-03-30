"""Parsing helpers for theorem-like LaTeX headers and bounded windows."""

from __future__ import annotations

import re

from ..models import LemmaHeader

THEOREM_LIKE_KEYWORDS = (
    "theorem",
    "thm",
    "lemma",
    "lem",
    "proposition",
    "prop",
    "corollary",
    "cor",
    "claim",
)
ENV_HEADER_RE = re.compile(r"\\+begin\{(?P<env>[^}]+)\}", flags=re.IGNORECASE)
VALID_ENV_TOKEN_RE = re.compile(r"^[a-z@][a-z0-9@:_-]*$", re.IGNORECASE)


def normalize_environment_token(token: str) -> str | None:
    normalized = token.strip().lower()
    while normalized.endswith("*"):
        normalized = normalized[:-1].strip()
    if not normalized or not VALID_ENV_TOKEN_RE.fullmatch(normalized):
        return None
    return normalized


def extract_environment_token(header_line: str) -> str | None:
    match = ENV_HEADER_RE.search(header_line)
    if not match:
        return None
    return normalize_environment_token(match.group("env"))


def extract_environment_name(header_line: str) -> str | None:
    return extract_environment_token(header_line)


def parse_grep_headers(raw_output: str) -> list[LemmaHeader]:
    headers: list[LemmaHeader] = []
    for line in raw_output.splitlines():
        if not line.strip() or ":" not in line:
            continue
        line_no_raw, content = line.split(":", 1)
        try:
            line_no = int(line_no_raw)
        except ValueError:
            continue
        headers.append(LemmaHeader(line_number=line_no, line=content.strip()))
    return headers


def window_bounds(line_number: int, total_lines: int, radius: int = 20) -> tuple[int, int]:
    clamped = min(max(1, line_number), total_lines)
    start = max(1, clamped - radius)
    end = min(total_lines, clamped + radius)
    return start, end
