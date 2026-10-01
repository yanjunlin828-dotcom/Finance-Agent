"""Read-only S5 tools bound to the verified corpus and financial source mapping."""
from __future__ import annotations
from dataclasses import dataclass, field
from finresearch.contracts import MetricObservation, EvidenceCandidate, stable_sha256
from finresearch.contracts.research import ResearchContext, ResearchClaim
from finresearch.contracts.retrieval import RetrievalQuery
from finresearch.contracts.supplement import SupplementAction, ToolResult
from finresearch.ingestion.pdf_pages import validate_evidence_pages
from finresearch.retrieval.corpus import ResearchCorpus
from .evidence_selection import evidence_window, quote_options


@dataclass
class SupplementTools:
    corpus: ResearchCorpus
    observations: list[MetricObservation]
    financial_evidence: list[EvidenceCandidate]
    retriever: object
    known_evidence: list[EvidenceCandidate] = field(default_factory=list)

    def verify_observation(self, context: ResearchContext, item: MetricObservation) -> bool:
        """Only a byte-equivalent independently source-built record is admissible."""
        return item in self.observations and item.company_id in context.company_ids and item.document_published_on <= context.as_of_date

    def verify_evidence(self, context: ResearchContext, item: EvidenceCandidate) -> bool:
        """Check original lines and manifest scope; vector/model metadata is not authority."""
        record = self.corpus.documents.get(item.document_id)
        if (record is None or self.corpus.snapshot_id != context.corpus_snapshot_id
                or record.ts_code != item.company_id or item.company_id not in context.company_ids
                or record.published_on is None or record.published_on > context.as_of_date
                or int(record.reporting_period) not in context.fiscal_years):
            return False
        validate_evidence_pages([item], list(self.corpus.pages.values()))
        return True

    def execute(self, context: ResearchContext, action: SupplementAction) -> ToolResult:
        """Fixed source read only; no SQL, file path or snapshot writes from an action."""
        if (action.company_id not in context.company_ids or action.as_of_date != context.as_of_date
                or action.corpus_snapshot_id != context.corpus_snapshot_id):
            raise ValueError("工具请求范围越界")
        if action.tool == "QUERY_METRIC" or action.tool == "REVIEW_TABLE" and "metric_id" in action.arguments:
            rows = [o for o in self.observations if o.company_id == action.company_id
                    and o.fiscal_year == action.arguments["fiscal_year"] and o.metric_id == action.arguments["metric_id"]
                    and o.document_published_on <= context.as_of_date]
            if len(rows) > 1:
                return ToolResult(action_id=action.action_id, status="NEEDS_INPUT", note="来源冲突需要裁决，不能静默取最新")
            row = rows[0] if rows else None
            ids = {r.evidence_id for r in row.evidence_refs} if row else set()
            return ToolResult(action_id=action.action_id, status="FOUND" if row else "NO_EVIDENCE",
                              observations=[row.model_dump(mode="json")] if row else [],
                              evidence=[e.model_dump(mode="json") for e in self.financial_evidence if e.evidence_id in ids],
                              note="读取当前快照的已复核指标；不回填新资料" if row else "当前快照没有该期间指标")
        if action.tool == "SEARCH_DISCLOSURE":
            query = RetrievalQuery(question=action.arguments["question"], company_id=action.company_id,
                                   reporting_year=action.arguments["reporting_year"], as_of_date=context.as_of_date,
                                   corpus_snapshot_id=context.corpus_snapshot_id)
            result = self.retriever.retrieve(query, "bm25")
            candidates = {}
            for hit in result.hits[:3]:
                window = evidence_window(self.corpus.pages[hit.chunk.parent_page_id], hit.chunk, hit.rank)
                if window:
                    candidates[window.evidence_id] = window
            return ToolResult(action_id=action.action_id, status="FOUND" if candidates else "NO_EVIDENCE",
                              evidence=[e.model_dump(mode="json") for e in candidates.values()],
                              note="已定位候选原文窗口，语义支持仍需独立审核" if candidates else "合法范围的当前查询未召回原文")
        if action.tool == "READ_CONTEXT":
            item = next((e for e in self.known_evidence + self.financial_evidence if e.evidence_id == action.arguments.get("evidence_id")), None)
            if item is None or item.company_id != action.company_id or not self.verify_evidence(context, item):
                return ToolResult(action_id=action.action_id, status="NEEDS_INPUT", note="证据ID未登记或范围不适用，不能读取任意文件")
            return ToolResult(action_id=action.action_id, status="FOUND", evidence=[item.model_dump(mode="json")],
                              note="返回已登记的完整原始行窗口，保留表头与否定；不改写")
        if action.tool == "REVIEW_TABLE":
            return ToolResult(action_id=action.action_id, status="REQUIRES_NEW_SNAPSHOT", note="表头/单位歧义须复核映射；改变权威值时生成新快照和派生运行")
        if action.tool == "VERIFY_VERSION":
            return ToolResult(action_id=action.action_id, status="NEEDS_INPUT", note="固定语料不足以认证所有更正版本，需要明确版本资料及公布日期")
        return ToolResult(action_id=action.action_id, status="NEEDS_INPUT", note="需要用户补充合法资料或澄清，当前运行不扩大权限")


def claims_from_quote_ids(ids: list[str], candidates: list[EvidenceCandidate]) -> list[ResearchClaim]:
    """Bind model-selected literal quote IDs to program-rendered attributed claims."""
    options = {o["quote_id"]: o for o in quote_options(candidates)}
    by_id = {e.evidence_id: e for e in candidates}
    if len(ids) > 6 or len(ids) != len(set(ids)) or any(i not in options for i in ids):
        raise ValueError("摘录ID不存在、重复或超限")
    return [ResearchClaim(claim_id="claim-s5-" + stable_sha256(options[i])[:20],
                          company_id=by_id[options[i]["evidence_id"]].company_id,
                          kind="DISCLOSED", text=f"公司披露：“{options[i]['exact_quote']}”", status="APPROVED",
                          evidence_ids=[options[i]["evidence_id"]], limitations=["公司原文披露，不构成独立因果核验"]) for i in ids]
