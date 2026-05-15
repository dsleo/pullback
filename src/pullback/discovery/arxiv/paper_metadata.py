from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PaperMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    cited_by_count: int | None = None

