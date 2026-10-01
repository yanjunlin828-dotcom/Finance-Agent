"""把已登记PDF幂等导入为逐页文本快照。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pdfplumber

from finresearch.contracts import DocumentManifestRecord, DocumentPage, EvidenceCandidate


@dataclass(frozen=True)
class SnapshotImportResult:
    snapshot_id: str
    document_id: str
    page_count: int
    pages_path: Path
    snapshot_manifest_path: Path
    reused_existing: bool
    pages_sha256: str


def _sha256_bytes(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_page_text(raw_text: str) -> str:
    """规范化Unicode、空白和断行，同时保留数字、符号与行顺序。"""

    normalized = unicodedata.normalize("NFKC", raw_text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)


def load_pages(path: Path) -> list[DocumentPage]:
    return [
        DocumentPage.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validate_page_set(pages: list[DocumentPage], record: DocumentManifestRecord, snapshot: dict[str, Any]) -> None:
    """Verify complete canonical pages against a trusted snapshot and disclosure.

    Reject changed text, identity, normalization or incomplete/duplicate pages.
    The trusted snapshot digest is supplied by the registry, not by the pages.
    """
    if snapshot.get("document_id") != record.document_id or snapshot.get("page_count") != record.page_count:
        raise ValueError("快照文档身份或页数不一致")
    if len(pages) != record.page_count or [p.pdf_page for p in pages] != list(range(1, record.page_count + 1)):
        raise ValueError("页集合不完整或页序重复")
    for page in pages:
        digest = _sha256_text(page.raw_text)
        if (page.document_id != record.document_id or page.company_id != record.ts_code
                or page.text_sha256 != digest or page.normalized_text != normalize_page_text(page.raw_text)
                or page.page_id != f"{record.document_id}:p{page.pdf_page:04d}:{digest[:16]}"):
            raise ValueError("页面身份、文本哈希或规范化内容不一致")
    canonical = "".join(p.model_dump_json() + "\n" for p in pages)
    if _sha256_text(canonical) != snapshot.get("pages_sha256"):
        raise ValueError("页面内容与登记快照SHA-256不一致")


def load_verified_pages(project_root: Path, snapshot: dict[str, Any], manifest: dict[str, Any]) -> list[DocumentPage]:
    """Load only an intact registered PDF/page snapshot inside the project root.

    File, page and parser identities are checked before returning any evidence.
    No I/O mutation or date inference occurs; request availability is checked by
    the workflow before calling this loader.
    """
    root = project_root.resolve()
    record = DocumentManifestRecord.model_validate(manifest)
    def inside(relative: str) -> Path:
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("快照路径超出项目根目录")
        return path
    if _sha256_bytes(inside(record.local_path)) != record.sha256:
        raise ValueError("原PDF SHA-256不一致")
    pages_path = inside(snapshot["pages_path"])
    saved = json.loads(inside(snapshot["snapshot_manifest_path"]).read_text(encoding="utf-8"))
    for key in ("snapshot_id", "document_id", "page_count", "pages_sha256"):
        if saved.get(key) != snapshot.get(key):
            raise ValueError(f"快照登记与Manifest不一致: {key}")
    if saved.get("document_sha256") != record.sha256 or _sha256_bytes(pages_path) != snapshot["pages_sha256"]:
        raise ValueError("快照或页文件SHA-256不一致")
    pages = load_pages(pages_path)
    validate_page_set(pages, record, snapshot)
    if any(p.parser_name != saved.get("parser_name") or p.parser_version != saved.get("parser_version") for p in pages):
        raise ValueError("快照解析器版本不一致")
    return pages


def validate_evidence_pages(candidates: list[EvidenceCandidate], pages: list[DocumentPage]) -> None:
    """Bind evidence text, hashes and locators to an already verified page set."""
    by_id = {p.page_id: p for p in pages}
    for item in candidates:
        page = by_id.get(item.page_id)
        if page is None:
            raise ValueError("证据页不属于已验证快照")
        lines = page.normalized_text.splitlines()
        text = "\n".join(lines[item.line_start - 1:item.line_end])
        if (item.line_end > len(lines) or text != item.text or _sha256_text(text) != item.evidence_sha256
                or item.document_id != page.document_id or item.company_id != page.company_id or item.pdf_page != page.pdf_page):
            raise ValueError("证据文本、定位或身份与页面不一致")


class PDFPageIngestor:
    """验证文件身份后生成不可混淆的逐页快照。"""

    def __init__(self, project_root: Path, storage_root: Path, snapshot_prefix: str = "s1") -> None:
        self.project_root = project_root.resolve()
        self.storage_root = storage_root.resolve()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,20}", snapshot_prefix):
            raise ValueError("snapshot_prefix格式无效")
        self.snapshot_prefix = snapshot_prefix

    def import_document(self, manifest_payload: dict[str, Any]) -> SnapshotImportResult:
        record = DocumentManifestRecord.model_validate(manifest_payload)
        pdf_path = (self.project_root / record.local_path).resolve()
        if not pdf_path.is_relative_to(self.project_root):
            raise ValueError("文档路径超出项目根目录")
        if not pdf_path.is_file():
            raise FileNotFoundError(pdf_path)
        actual_hash = _sha256_bytes(pdf_path)
        if actual_hash != record.sha256:
            raise ValueError("原PDF SHA-256与DocumentManifest不一致")

        snapshot_id = (
            f"{self.snapshot_prefix}-{record.snapshot_id}-{record.sha256[:12]}-pdfplumber-{pdfplumber.__version__}"
        )
        snapshot_dir = (self.storage_root / snapshot_id).resolve()
        if not snapshot_dir.is_relative_to(self.storage_root):
            raise ValueError("快照身份导致路径超出storage_root")
        pages_path = snapshot_dir / "pages.jsonl"
        snapshot_manifest_path = snapshot_dir / "snapshot.json"
        if pages_path.is_file() and snapshot_manifest_path.is_file():
            existing = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
            pages_hash = _sha256_bytes(pages_path)
            if (
                existing.get("document_sha256") == record.sha256
                and existing.get("document_id") == record.document_id
                and existing.get("parser_version") == pdfplumber.__version__
                and existing.get("pages_sha256") == pages_hash
                and existing.get("page_count") == record.page_count
            ):
                validate_page_set(load_pages(pages_path), record, existing)
                return SnapshotImportResult(
                    snapshot_id=snapshot_id,
                    document_id=record.document_id,
                    page_count=record.page_count,
                    pages_path=pages_path,
                    snapshot_manifest_path=snapshot_manifest_path,
                    reused_existing=True,
                    pages_sha256=pages_hash,
                )
            raise RuntimeError("已存在的页面快照与当前输入不一致，拒绝覆盖")

        self.storage_root.mkdir(parents=True, exist_ok=True)
        working_dir = (self.storage_root / f".{snapshot_id}.tmp-{os.getpid()}").resolve()
        if not working_dir.is_relative_to(self.storage_root):
            raise ValueError("临时快照路径超出storage_root")
        if working_dir.exists():
            shutil.rmtree(working_dir)
        working_dir.mkdir(parents=False)
        working_pages_path = working_dir / "pages.jsonl"
        working_manifest_path = working_dir / "snapshot.json"
        try:
            pages: list[DocumentPage] = []
            with pdfplumber.open(pdf_path) as pdf:
                if len(pdf.pages) != record.page_count:
                    raise ValueError("pdfplumber页数与DocumentManifest不一致")
                for index, pdf_page in enumerate(pdf.pages, start=1):
                    raw_text = pdf_page.extract_text(layout=True) or ""
                    normalized_text = normalize_page_text(raw_text)
                    text_sha = _sha256_text(raw_text)
                    page_id = f"{record.document_id}:p{index:04d}:{text_sha[:16]}"
                    pages.append(
                        DocumentPage(
                            page_id=page_id,
                            document_id=record.document_id,
                            company_id=record.ts_code,
                            pdf_page=index,
                            printed_page=None,
                            raw_text=raw_text,
                            normalized_text=normalized_text,
                            text_sha256=text_sha,
                            parser_name="pdfplumber",
                            parser_version=pdfplumber.__version__,
                            extraction_status="TEXT_AVAILABLE" if normalized_text else "EMPTY",
                        )
                    )

            with NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=working_dir) as temporary:
                temporary_path = Path(temporary.name)
                for page in pages:
                    temporary.write(page.model_dump_json() + "\n")
            os.replace(temporary_path, working_pages_path)
            pages_sha = _sha256_bytes(working_pages_path)
            snapshot_manifest = {
                "schema_version": "1.0.0",
                "snapshot_id": snapshot_id,
                "document_id": record.document_id,
                "document_sha256": record.sha256,
                "page_count": len(pages),
                "nonempty_page_count": sum(page.extraction_status == "TEXT_AVAILABLE" for page in pages),
                "parser_name": "pdfplumber",
                "parser_version": pdfplumber.__version__,
                "pages_sha256": pages_sha,
                "pages_file": "pages.jsonl",
            }
            working_manifest_path.write_text(
                json.dumps(snapshot_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(working_dir, snapshot_dir)
        except Exception:
            shutil.rmtree(working_dir, ignore_errors=True)
            raise
        return SnapshotImportResult(
            snapshot_id=snapshot_id,
            document_id=record.document_id,
            page_count=len(pages),
            pages_path=pages_path,
            snapshot_manifest_path=snapshot_manifest_path,
            reused_existing=False,
            pages_sha256=pages_sha,
        )
