"""S0在线模型试验将使用的供应商无关输出契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConnectivityProbeOutput(BaseModel):
    """T05只验证结构化响应与请求nonce是否往返。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["OK"]
    nonce: str = Field(min_length=8, max_length=100)
    message: str = Field(min_length=1, max_length=500)


class EvidenceReference(BaseModel):
    """模型答案引用的既有证据，不允许自由提供本地路径。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=3, max_length=150)
    document_id: str = Field(min_length=3, max_length=150)
    pdf_page: int = Field(gt=0)


class EvidenceAnswer(BaseModel):
    """T06/T07的最小回答格式。

    可回答时必须有答案和证据；证据不足时必须列出所缺信息，且不得在另一字段
    偷塞一个看似确定的答案。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ANSWERABLE", "INSUFFICIENT_EVIDENCE"]
    answer: str | None = Field(default=None, max_length=4000)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    period: str | None = Field(default=None, max_length=100)
    unit: str | None = Field(default=None, max_length=100)
    missing_information: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_requirements(self) -> "EvidenceAnswer":
        if self.status == "ANSWERABLE":
            if not self.answer or not self.evidence_refs:
                raise ValueError("ANSWERABLE必须同时包含answer和evidence_refs")
        else:
            if self.answer is not None:
                raise ValueError("INSUFFICIENT_EVIDENCE不得包含确定答案")
            if not self.missing_information:
                raise ValueError("INSUFFICIENT_EVIDENCE必须列出missing_information")
        return self


class PageCountArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=3, max_length=150)


class ToolRequest(BaseModel):
    """T08允许的唯一工具请求；未知工具名在执行前即被拒绝。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: Literal["get_document_page_count"]
    arguments: PageCountArguments
