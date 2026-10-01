"""S0 研究请求契约。

该模块只解决“研究对象、期间和截止日是否明确”。它不做检索、财务计算或
投资判断。序列化时使用 ISO 日期；进入运行时后统一转换成 ``pd.Timestamp``。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .document_manifest import DocumentManifestRecord


class ScopeViolation(ValueError):
    """请求通过字段校验、但违反当前范围合同时抛出。"""


class ResearchRequest(BaseModel):
    """可确定复现的专题研究请求。

    时间对齐假设：A 股公开文件首版只处理日级披露时间；截止日含义固定为
    ``Asia/Shanghai`` 当天结束。若只有日期而无可靠时刻，不支持盘前判断。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    topic_id: Literal["revenue_quality_v0"]
    company_ids: list[str] = Field(min_length=1)
    reporting_years: list[int] = Field(min_length=1)
    as_of_date: date
    cutoff_policy: Literal["ASIA_SHANGHAI_END_OF_DAY"]
    timezone: Literal["Asia/Shanghai"]
    document_snapshot_id: str = Field(min_length=3, max_length=100)
    question: str = Field(min_length=3, max_length=2000)
    output_language: Literal["zh-CN"]
    budget_profile_id: str = Field(min_length=3, max_length=100)

    @field_validator("company_ids")
    @classmethod
    def validate_company_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("company_ids 不得重复")
        invalid = [item for item in value if not _is_ts_code(item)]
        if invalid:
            raise ValueError(f"company_ids 必须使用带交易所后缀的证券代码: {invalid}")
        return value

    @field_validator("reporting_years")
    @classmethod
    def validate_reporting_years(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("reporting_years 不得重复")
        if any(year < 2000 or year > 2100 for year in value):
            raise ValueError("reporting_years 超出支持范围 2000—2100")
        return sorted(value)

    @property
    def as_of_timestamp(self) -> pd.Timestamp:
        """返回研究时区的日终时间戳，供后续比较使用。"""

        return pd.Timestamp(self.as_of_date, tz=self.timezone) + pd.Timedelta(days=1) - pd.Timedelta(
            nanoseconds=1
        )


def validate_request_against_scope(request: ResearchRequest, scope: dict[str, Any]) -> None:
    """校验请求是否属于当前 S0 范围合同。

    输入：已通过 Pydantic 字段校验的请求、从 ``scope.json`` 读取的字典。
    输出：无返回值；越界时抛出带具体原因的 ``ScopeViolation``。
    """

    allowed_topics = set(scope["allowed_topic_ids"])
    allowed_companies = set(scope["pilot_scope"]["company_ids"])
    allowed_years = set(scope["pilot_scope"]["reporting_years"])
    snapshot = scope["document_snapshot"]

    problems: list[str] = []
    if request.topic_id not in allowed_topics:
        problems.append(f"topic_id 未获范围合同支持: {request.topic_id}")
    if not set(request.company_ids).issubset(allowed_companies):
        problems.append("company_ids 包含先行范围之外的公司")
    if not set(request.reporting_years).issubset(allowed_years):
        problems.append("reporting_years 包含先行范围之外的财年")
    if request.document_snapshot_id != snapshot["snapshot_id"]:
        problems.append("document_snapshot_id 与范围合同不一致")
    if problems:
        raise ScopeViolation("；".join(problems))


def select_available_documents(
    request: ResearchRequest,
    manifest_records: list[dict[str, Any]],
    allowed_document_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """按公司、财年、快照和披露日筛出截止日可用文档。

    日期未知的文件一律不纳入。日级披露文件在披露当天日终请求中可用；
    抓取时间和报告期均不参与可用性判断。
    """

    available: list[dict[str, Any]] = []
    requested_companies = set(request.company_ids)
    requested_years = {str(year) for year in request.reporting_years}
    for raw_record in manifest_records:
        validated = DocumentManifestRecord.model_validate(raw_record)
        record = validated.model_dump(mode="json")
        if allowed_document_ids is not None and record["document_id"] not in allowed_document_ids:
            continue
        if record.get("ts_code") not in requested_companies:
            continue
        if str(record.get("reporting_period")) not in requested_years:
            continue
        if record.get("snapshot_id") != request.document_snapshot_id:
            continue
        published_on = record.get("published_on")
        if not published_on:
            continue
        published_date = pd.Timestamp(published_on).date()
        if published_date <= request.as_of_date:
            available.append(record)
    return available


def _is_ts_code(value: str) -> bool:
    if len(value) != 9 or value[6] != ".":
        return False
    return value[:6].isdigit() and value[7:] in {"SZ", "SH", "BJ"}
