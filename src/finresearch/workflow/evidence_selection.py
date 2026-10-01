"""Shared bounded literal quote selection and original-line evidence windows."""
from __future__ import annotations
import hashlib
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from finresearch.contracts import EvidenceCandidate
from finresearch.contracts.research import DisclosureBatch, Category


class QuoteChoice(BaseModel):
    model_config=ConfigDict(extra="forbid")
    quote_id: str
    related_hypothesis_categories: list[Category]=Field(default_factory=list,max_length=3)
    relation: Literal["SUPPORTING_DISCLOSURE","WEAKENING_DISCLOSURE","CONTEXT"]="CONTEXT"


class QuoteChoiceBatch(BaseModel):
    model_config=ConfigDict(extra="forbid")
    company_id: str
    selected: list[QuoteChoice]=Field(max_length=3)
    missing_information: list[str]=Field(default_factory=list,max_length=6)


def quote_options(candidates):
    """Bounded complete sentences, excluding unchecked form propositions.

    Never shorten a sentence to satisfy length. Full surrounding evidence is
    retained. This is a syntactic guard, not universal semantic certification.
    """
    options=[]
    for evidence in candidates:
        for match in re.finditer(r"[^。！？]+[。！？]",evidence.text):
            text=match.group().strip()
            # A first sentence can follow table rows or begin before the window.
            # Explicit report labels/numbered paragraphs identify a nearer,
            # intact start; they are literal source boundaries, not paraphrases.
            anchors=[m.start() for m in re.finditer(r"(?:营业收入变动原因说明|经营活动产生的现金流量净额变动原因说明|\n\(\d+\))",text)]
            if anchors:
                text=text[max(anchors):].strip()
            if 5<=len(text)<=250 and not re.search(r"[□√☑☐\uf052]",text):
                options.append({"quote_id":hashlib.sha256((evidence.evidence_id+text).encode()).hexdigest()[:20],
                    "evidence_id":evidence.evidence_id,"exact_quote":text})
    return options


def bind_quote_choices(batch,candidates,options,company):
    if batch.company_id!=company:
        raise ValueError("摘录选择公司错误")
    by_id={o["quote_id"]:o for o in options}
    selected=[]
    seen=set()
    for choice in batch.selected:
        if choice.quote_id not in by_id or choice.quote_id in seen:
            raise ValueError("摘录ID不存在或重复")
        seen.add(choice.quote_id)
        item=by_id[choice.quote_id]
        selected.append({"evidence_id":item["evidence_id"],"exact_quote":item["exact_quote"],
            "related_hypothesis_categories":choice.related_hypothesis_categories,"relation":choice.relation})
    return DisclosureBatch(company_id=company,selected=selected,missing_information=batch.missing_information)


def evidence_window(page, chunk, rank):
    """Whole original lines around the hit; never truncate or paraphrase a quote."""
    lines = page.normalized_text.splitlines()
    start, end = max(1, chunk.line_start - 2), min(len(lines), chunk.line_end + 3)
    while len("\n".join(lines[start-1:end])) > 1100 and (start < chunk.line_start or end > chunk.line_end):
        if end > chunk.line_end:
            end -= 1
        elif start < chunk.line_start:
            start += 1
    text = "\n".join(lines[start-1:end])
    if not text.strip() or len(text) > 1100:
        return None  # Explicit retained skipped-span record; no silent truncation.
    digest = hashlib.sha256(text.encode()).hexdigest()
    return EvidenceCandidate(evidence_id=f"{page.document_id}:p{page.pdf_page:04d}:l{start}-{end}:e{digest[:12]}",
        document_id=page.document_id, page_id=page.page_id, company_id=page.company_id, pdf_page=page.pdf_page,
        line_start=start, line_end=end, text=text, matched_terms=[], retrieval_score=0, retrieval_rank=rank, evidence_sha256=digest)


