"""S0文档清单契约。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DocumentManifestRecord(BaseModel):
    """一个已经登记、可通过内容指纹识别的公开文件版本。

    ``published_on`` 表示资料公开日期，``reporting_period`` 表示资料描述期间，
    ``retrieved_at`` 只表示本地抓取时间；三者不可互相替代。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=3, max_length=150)
    snapshot_id: str = Field(min_length=3, max_length=100)
    company_id: str = Field(min_length=2, max_length=100)
    ts_code: str
    title: str = Field(min_length=3)
    document_type: str = Field(min_length=3)
    reporting_period: str = Field(pattern=r"^\d{4}$")
    published_on: date | None
    time_precision: Literal["DATE", "DATETIME", "UNKNOWN"]
    date_status: str = Field(min_length=3)
    date_evidence_ref: str | None
    landing_url: str
    source_url: str
    retrieved_at: datetime
    local_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    page_count: int = Field(gt=0)
    version_status: str = Field(min_length=3)
    supersedes_id: str | None
    correction_search_status: str = Field(min_length=3)
    usage_status: str = Field(min_length=3)
    extraction_status: str = Field(min_length=3)
    parser_version: str = Field(min_length=3)

    @field_validator("ts_code")
    @classmethod
    def validate_ts_code(cls, value: str) -> str:
        if len(value) != 9 or value[6] != "." or not value[:6].isdigit() or value[7:] not in {"SZ", "SH", "BJ"}:
            raise ValueError("ts_code必须使用带交易所后缀的证券代码")
        return value

    @field_validator("landing_url", "source_url")
    @classmethod
    def validate_https_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("正式来源URL必须使用https")
        return value

    @field_validator("local_path")
    @classmethod
    def validate_local_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("local_path必须是项目内安全相对路径")
        return normalized

    @model_validator(mode="after")
    def validate_date_semantics(self) -> "DocumentManifestRecord":
        if self.published_on is None:
            if self.time_precision != "UNKNOWN":
                raise ValueError("published_on未知时time_precision必须为UNKNOWN")
            if self.date_evidence_ref is not None:
                raise ValueError("published_on未知时不得伪造date_evidence_ref")
        elif self.time_precision == "UNKNOWN":
            raise ValueError("published_on已知时time_precision不能为UNKNOWN")
        if self.published_on is not None and self.date_evidence_ref is None:
            raise ValueError("已知披露日期必须提供date_evidence_ref")
        return self
