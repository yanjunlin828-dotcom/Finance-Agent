"""All model interactions are fake; no credential or network is used."""
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from finresearch.contracts import ConnectivityProbeOutput
from finresearch.model import DeepSeekJsonClient, LiveProbeGuard, ModelCallFailure, ProbeBudgetExceeded

ROOT = Path(__file__).resolve().parents[2]


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def bind(self, **options):
        return self

    def invoke(self, messages):
        self.calls += 1
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return value


def response(content, usage=True):
    return SimpleNamespace(content=content, response_metadata={}, tool_calls=[],
                           usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120} if usage else {})


def client(monkeypatch, replies, **overrides):
    config = json.loads((ROOT / "configs/s0/model_probe.json").read_text(encoding="utf-8"))
    config.update(overrides)
    fake = FakeModel(replies)
    monkeypatch.setattr("finresearch.model.deepseek_json.ChatOpenAI", lambda **kwargs: fake)
    sink = []
    guard = LiveProbeGuard(config)
    adapter = DeepSeekJsonClient(config, "offline-secret", guard, audit_sink=sink.append)
    return adapter, fake, guard, sink


VALID = '{"status":"OK","nonce":"nonce-123","message":"fine"}'


def test_failed_json_is_counted_costed_and_retried_once(monkeypatch):
    adapter, fake, guard, sink = client(monkeypatch, [response("invalid"), response(VALID)])
    parsed, audit = adapter.invoke_json("T05", ConnectivityProbeOutput, "test", "n")
    assert parsed.nonce == "nonce-123"
    assert fake.calls == guard.total_calls == len(sink) == 2
    assert [a["status"] for a in sink] == ["INVALID_STRUCTURED_OUTPUT", "PARSED"]
    assert sink[0]["content"] == "invalid"
    assert guard.reserved_cost == Decimal("0.00010800")


def test_all_malformed_outputs_have_final_failure_and_usage(monkeypatch):
    adapter, fake, guard, sink = client(monkeypatch, [response("bad1"), response("bad2")])
    with pytest.raises(ModelCallFailure) as failure:
        adapter.invoke_json("T05", ConnectivityProbeOutput, "test", "n")
    assert len(failure.value.audits) == len(sink) == guard.total_calls == 2
    assert failure.value.audits[1]["usage"]["total_tokens"] == 120


def test_timeout_has_no_automatic_retry_and_unknown_cost_stays_reserved(monkeypatch):
    adapter, fake, guard, sink = client(monkeypatch, [TimeoutError("offline-secret")])
    with pytest.raises(ModelCallFailure, match="TRANSPORT_FAILED"):
        adapter.invoke_json("T05", ConnectivityProbeOutput, "test", "n")
    assert fake.calls == guard.total_calls == len(sink) == 1
    assert guard.reserved_cost == Decimal("0.01")
    assert sink[0]["estimated_cost_usd"] is None
    assert "offline-secret" not in json.dumps(sink)


def test_input_cap_counts_schema_and_rejects_before_call(monkeypatch):
    adapter, fake, guard, sink = client(monkeypatch, [], maximum_input_tokens=30)
    with pytest.raises(ModelCallFailure, match="INPUT_BUDGET_EXCEEDED"):
        adapter.invoke_json("T05", ConnectivityProbeOutput, "", "")
    assert fake.calls == guard.total_calls == len(sink) == 0


def test_no_usage_is_never_relabelled_zero_cost(monkeypatch):
    adapter, fake, guard, sink = client(monkeypatch, [response(VALID, usage=False)])
    _, audit = adapter.invoke_json("T05", ConnectivityProbeOutput, "test", "n")
    assert audit["estimated_cost_usd"] is None
    assert guard.reserved_cost == Decimal("0.01")


def test_overrun_is_retained_and_stops_future_requests(monkeypatch):
    excessive = response(VALID)
    excessive.usage_metadata["input_tokens"] = 1_000_000
    adapter, fake, guard, sink = client(monkeypatch, [excessive])
    with pytest.raises(ModelCallFailure, match="COST_BUDGET_EXCEEDED"):
        adapter.invoke_json("T05", ConnectivityProbeOutput, "test", "n")
    assert guard.reserved_cost > Decimal("0.10")
    assert sink[0]["status"] == "COST_BUDGET_EXCEEDED"
    with pytest.raises(ProbeBudgetExceeded):
        adapter.invoke_json("T05", ConnectivityProbeOutput, "test", "n")
    assert fake.calls == 1


def test_text_credential_echo_is_redacted(monkeypatch):
    adapter, fake, guard, sink = client(monkeypatch, [response("offline-secret"), response("bad")])
    with pytest.raises(ModelCallFailure):
        adapter.invoke_json("T05", ConnectivityProbeOutput, "test", "n")
    assert sink[0]["content"] == "[REDACTED]"


def test_exhausted_budget_blocks_retry_after_recording_first_failure(monkeypatch):
    adapter, fake, guard, sink = client(monkeypatch, [response("bad")], maximum_total_calls=1)
    with pytest.raises(ProbeBudgetExceeded):
        adapter.invoke_json("T05", ConnectivityProbeOutput, "test", "n")
    assert len(sink) == fake.calls == guard.total_calls == 1


def test_settlement_is_once_only(monkeypatch):
    adapter, fake, guard, sink = client(monkeypatch, [])
    reservation = guard.reserve_call("T05", Decimal("0.01"))
    guard.settle_call(reservation, None)
    with pytest.raises(ValueError, match="已结算"):
        guard.settle_call(reservation, Decimal("0"))
