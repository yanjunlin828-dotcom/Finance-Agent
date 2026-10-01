from __future__ import annotations

import pytest
from pydantic import ValidationError

from finresearch.contracts import ConnectivityProbeOutput, EvidenceAnswer, ToolRequest


def test_connectivity_output_requires_exact_status_and_nonce() -> None:
    output = ConnectivityProbeOutput(status="OK", nonce="s0-nonce-001", message="connected")
    assert output.nonce == "s0-nonce-001"
    with pytest.raises(ValidationError):
        ConnectivityProbeOutput(status="MAYBE", nonce="short", message="connected")


def test_answerable_output_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence_refs"):
        EvidenceAnswer(status="ANSWERABLE", answer="298.38亿元")


def test_insufficient_output_requires_missing_information_and_no_answer() -> None:
    valid = EvidenceAnswer(
        status="INSUFFICIENT_EVIDENCE",
        missing_information=["给定片段不含应收账款"],
        limitations=["只判断当前片段，不判断整份年报"],
    )
    assert valid.answer is None
    with pytest.raises(ValidationError, match="不得包含确定答案"):
        EvidenceAnswer(
            status="INSUFFICIENT_EVIDENCE",
            answer="可能是60亿元",
            missing_information=["缺原表"],
        )


def test_tool_request_rejects_unknown_name_and_extra_path() -> None:
    valid = ToolRequest.model_validate(
        {"tool_name": "get_document_page_count", "arguments": {"document_id": "doc-001"}}
    )
    assert valid.arguments.document_id == "doc-001"
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            {"tool_name": "read_any_path", "arguments": {"document_id": "doc-001", "path": "C:/secret"}}
        )
