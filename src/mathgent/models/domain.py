"""Domain models for extracted headers, matches, and per-paper search entries."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LemmaHeader(BaseModel):
    line_number: int = Field(ge=1)
    line: str


class LemmaMatch(BaseModel):
    arxiv_id: str
    line_number: int = Field(ge=1)
    header_line: str
    snippet: str
    score: float = Field(ge=0.0, le=1.0)


class SearchResultEntry(BaseModel):
    arxiv_id: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    match: LemmaMatch | None = None
    candidates: list[LemmaMatch] = Field(default_factory=list)
