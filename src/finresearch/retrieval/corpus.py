"""Immutable multi-document corpus loading and exact-span chunking."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finresearch.contracts import DocumentManifestRecord, DocumentPage
from finresearch.contracts.retrieval import RetrievalChunk, RetrievalQuery
from finresearch.ingestion.pdf_pages import load_verified_pages


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: object) -> None:
    """Create a new artifact; identical reuse is allowed, changed content is not."""
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != serialized:
            raise FileExistsError(f"不可变产物已经存在: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(serialized)


class ResearchCorpus:
    """Load a content-checked READY corpus, validating all PDF/page bindings.

    ``registry`` must be an explicitly selected, input-locked artifact. File and
    content checks detect accidental corruption, not an attacker rewriting all
    trusted inputs. Research date filtering occurs before any ranking.
    """
    def __init__(self, root: Path, registry: dict) -> None:
        if registry["status"] != "READY":
            raise ValueError("语料尚未READY")
        self.snapshot_id = registry["corpus_snapshot_id"]
        self.documents: dict[str, DocumentManifestRecord] = {}
        self.pages: dict[str, DocumentPage] = {}
        self.registry = registry
        for entry in registry["documents"]:
            if entry["status"] != "READY" or not entry["content_checks"]:
                raise ValueError("文档尚未完成内容验收")
            record = DocumentManifestRecord.model_validate(entry["manifest"])
            if record.document_id in self.documents or record.published_on is None:
                raise ValueError("文档重复或披露日缺失")
            pages = load_verified_pages(root, entry["snapshot"], entry["manifest"])
            by_number = {p.pdf_page: p for p in pages}
            for check in entry["content_checks"]:
                if not check["excerpts"] or not all(x in by_number[check["pdf_page"]].normalized_text for x in check["excerpts"]):
                    raise ValueError("内容验收摘录与正式页快照不一致")
            self.documents[record.document_id] = record
            self.pages.update({p.page_id: p for p in pages})

    def eligible_documents(self, query: RetrievalQuery) -> list[str]:
        """A single mandatory filter shared by every scorer and context read."""
        if query.corpus_snapshot_id != self.snapshot_id:
            raise ValueError("请求快照与语料不一致")
        return sorted(doc_id for doc_id, r in self.documents.items()
                      if r.ts_code == query.company_id
                      and int(r.reporting_period) == query.reporting_year
                      and r.document_type == query.document_type
                      and r.published_on is not None and r.published_on <= query.as_of_date)


def chunk_pages(corpus: ResearchCorpus, config: dict) -> list[RetrievalChunk]:
    """Split canonical page text into reproducible spans without lost tails.

    Offsets index normalized text, end is exclusive. Prefer newline boundaries;
    split an overlong line using exact character offsets. Overlap up to two
    lines, capped below half a chunk so every iteration makes progress.
    """
    maximum = int(config["max_chunk_characters"])
    if maximum < 50:
        raise ValueError("块长度过小")
    chunks: list[RetrievalChunk] = []
    for page in sorted(corpus.pages.values(), key=lambda p: (p.document_id, p.pdf_page)):
        record = corpus.documents[page.document_id]
        text = page.normalized_text
        start = 0
        ordinal = 0
        while start < len(text):
            end = min(start + maximum, len(text))
            boundary = text.rfind("\n", start, end)
            if end < len(text) and boundary > start + maximum // 2:
                end = boundary + 1
            value = text[start:end]
            if value.strip():
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
                chunks.append(RetrievalChunk(
                    chunk_id=f"{page.page_id}:c{ordinal:03d}:{digest[:12]}",
                    parent_page_id=page.page_id, document_id=page.document_id,
                    company_id=record.ts_code, reporting_year=int(record.reporting_period),
                    published_on=record.published_on, document_type=record.document_type,
                    document_status="READY", corpus_snapshot_id=corpus.snapshot_id,
                    source_sha256=record.sha256, pdf_page=page.pdf_page,
                    character_start=start, character_end=end,
                    line_start=text.count("\n", 0, start) + 1,
                    line_end=text.count("\n", 0, end - 1) + 1,
                    text=value, text_sha256=digest, chunker_version=config["chunker_version"],
                ))
                ordinal += 1
            if end == len(text):
                break
            previous_lines = text[start:end].splitlines(keepends=True)
            overlap = len("".join(previous_lines[-int(config["overlap_lines"]):])) if config["overlap_lines"] else 0
            start = end - min(overlap, maximum // 3)
    return chunks
