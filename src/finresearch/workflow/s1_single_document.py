"""S1单文档工作流的确定性前置检查与证据准备。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from finresearch.contracts import DocumentPage, ResearchRequest, RetrievalResult, DocumentManifestRecord
from finresearch.ingestion import validate_page_set, validate_evidence_pages
from finresearch.contracts.research_request import (
    ScopeViolation,
    select_available_documents,
    validate_request_against_scope,
)
from finresearch.retrieval import KeywordEvidenceRetriever


class PreflightFailure(RuntimeError):
    """在读取页面或调用模型前即可确定的阻断错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedEvidence:
    request: ResearchRequest
    document: dict[str, Any]
    retrieval: RetrievalResult


def prepare_single_document_request(
    request: ResearchRequest,
    *,
    scope: dict[str, Any],
    manifest_records: list[dict[str, Any]],
    allowed_document_id: str,
    snapshot_record: dict[str, Any],
    page_loader: Callable[[], list[DocumentPage]],
    retriever: KeywordEvidenceRetriever,
) -> PreparedEvidence:
    """先验证范围和披露日，再加载正文并生成候选证据。

    时间对齐：仅使用 ``published_on <= request.as_of_date`` 的文件。数据依赖：
    已验证Manifest、S1页面快照和版本化检索配置。前置校验失败时不会执行
    ``page_loader``，因此也不会进入后续模型调用。
    """

    try:
        validate_request_against_scope(request, scope)
    except ScopeViolation as exc:
        raise PreflightFailure("OUT_OF_SCOPE", str(exc)) from exc

    requested_company = request.company_ids[0]
    requested_year = str(request.reporting_years[0])
    target = next((item for item in manifest_records if item["document_id"] == allowed_document_id), None)
    if target is None:
        raise PreflightFailure("DOCUMENT_NOT_REGISTERED", "目标文档未登记")
    if target.get("ts_code") != requested_company or str(target.get("reporting_period")) != requested_year:
        raise PreflightFailure("OUT_OF_SCOPE", "目标文档与请求公司或财年不一致")

    available = select_available_documents(request, manifest_records, {allowed_document_id})
    if not available:
        raise PreflightFailure("NOT_AVAILABLE_AS_OF", "截至请求日该文件尚不可用")

    try:
        pages = page_loader()
        validate_page_set(pages, DocumentManifestRecord.model_validate(target), snapshot_record)
    except (ValueError, OSError) as exc:
        raise PreflightFailure("DOCUMENT_INTEGRITY_FAILURE", "页面快照完整性检查失败") from exc
    try:
        retrieval = retriever.retrieve(request.request_id, request.question, pages)
        validate_evidence_pages(retrieval.evidence_candidates, pages)
    except ValueError as exc:
        raise PreflightFailure("UNSAFE_EVIDENCE_WINDOW", "无法构造完整且可核对的证据窗口") from exc
    if retrieval.status != "FOUND" or not retrieval.evidence_candidates:
        raise PreflightFailure("INSUFFICIENT_EVIDENCE", "当前文档未检索到足够证据")
    return PreparedEvidence(request=request, document=available[0], retrieval=retrieval)
