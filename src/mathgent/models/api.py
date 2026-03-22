"""API request/response models exposed by the FastAPI surface."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .domain import SearchResultEntry


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=20)
    strictness: float = Field(default=0.2, ge=0.0, le=1.0)


class SearchResponse(BaseModel):
    query: str
    max_results: int
    strictness: float
    results: list[SearchResultEntry]
