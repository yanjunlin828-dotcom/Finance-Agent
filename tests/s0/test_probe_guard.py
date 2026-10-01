from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from finresearch.model import LiveProbeGuard, LiveProbeNotReady, ProbeBudgetExceeded, redact_sensitive

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_config() -> dict:
    return json.loads((PROJECT_ROOT / "configs/s0/model_probe.json").read_text(encoding="utf-8"))


def ready_config() -> dict:
    config = deepcopy(load_config())
    config.update(
        {
            "status": "READY_FOR_LIVE_PROBE",
            "provider": "test-provider",
            "model": "test-model",
            "credential_present": True,
            "approved_external_data_scope": ["evidence-fragment-001"],
            "currency_limit": "1.00",
            "single_call_timeout_seconds": 30,
            "maximum_input_tokens": 1000,
            "maximum_output_tokens": 300,
        }
    )
    return config


def test_empty_budget_config_blocks_live_calls() -> None:
    config = ready_config()
    config["currency_limit"] = None
    guard = LiveProbeGuard(config)
    with pytest.raises(LiveProbeNotReady, match="currency_limit未配置"):
        guard.reserve_call("T05", Decimal("0.01"))


def test_current_live_probe_config_is_complete() -> None:
    guard = LiveProbeGuard(load_config())
    assert guard.readiness_problems() == []


def test_call_and_retry_limits_are_enforced() -> None:
    guard = LiveProbeGuard(ready_config())
    guard.reserve_call("T05", Decimal("0.10"))
    guard.reserve_call("T05", Decimal("0.10"))
    with pytest.raises(ProbeBudgetExceeded, match="单试验"):
        guard.reserve_call("T05", Decimal("0.10"))


def test_currency_and_probe_whitelist_are_enforced() -> None:
    config = ready_config()
    config["currency_limit"] = "0.15"
    guard = LiveProbeGuard(config)
    guard.reserve_call("T06", Decimal("0.10"))
    with pytest.raises(ProbeBudgetExceeded, match="货币上限"):
        guard.reserve_call("T07", Decimal("0.10"))
    with pytest.raises(ProbeBudgetExceeded, match="白名单"):
        LiveProbeGuard(ready_config()).reserve_call("T99", Decimal("0.01"))


def test_redaction_hides_credentials_but_keeps_usage_counts() -> None:
    value = {
        "authorization": "Bearer secret",
        "nested": {"api_key": "abc", "input_tokens": 123, "maximum_input_tokens": 1000},
    }
    redacted = redact_sensitive(value)
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["input_tokens"] == 123
    assert redacted["nested"]["maximum_input_tokens"] == 1000
