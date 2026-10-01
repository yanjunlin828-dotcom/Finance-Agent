"""Strict S3 multi-document retrieval contracts; time is disclosure-date based."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    question: str = Field(min_length=2, max_length=2000)
    company_id: str = Field(pattern=r"^\d{6}\.(SZ|SH|BJ)$")
    reporting_year: int = Field(ge=2000, le=2100)
    as_of_date: date
    corpus_snapshot_id: str = Field(min_length=3)
    document_type: Literal["ANNUAL_REPORT_FULL"] = "ANNUAL_REPORT_FULL"
    top_k: int = Field(default=5, ge=1, le=20)


class RetrievalChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chunk_id: str
    parent_page_id: str
    document_id: str
    company_id: str
    reporting_year: int
    published_on: date
    document_type: Literal["ANNUAL_REPORT_FULL"]
    document_status: Literal["READY"]
    corpus_snapshot_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_page: int = Field(gt=0)
    character_start: int = Field(ge=0)
    character_end: int = Field(gt=0)
    line_start: int = Field(gt=0)
    line_end: int = Field(gt=0)
    text: str = Field(min_length=1)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunker_version: str


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rank: int = Field(gt=0)
    score: float
    chunk: RetrievalChunk
    # Entire parent text retains table headings, context and footnotes.
    parent_text: str
    adjacent_context: list[dict[str, object]] = Field(default_factory=list)
    contributing_ranks: dict[str, int]
    matched_terms: list[str] = Field(default_factory=list)


class MultiDocumentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: RetrievalQuery
    method: Literal["keyword", "bm25", "vector", "hybrid"]
    status: Literal["FOUND", "NO_EVIDENCE", "NO_ELIGIBLE_DOCUMENT"]
    eligible_documents: list[str]
    candidate_chunk_ids: list[str]
    hits: list[RetrievalHit]
    elapsed_ms: float = Field(ge=0)
