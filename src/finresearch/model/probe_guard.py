"""S0真实模型试验的配置、次数和日志防护。"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from typing import Any


class LiveProbeNotReady(RuntimeError):
    """真实调用所需配置尚未明确。"""


class ProbeBudgetExceeded(RuntimeError):
    """调用次数、重试次数或货币上限将被突破。"""


class LiveProbeGuard:
    """在供应商适配器之前阻止未授权或超预算的在线调用。

    空货币上限不会被解释成无限预算。调用方必须先执行 ``assert_ready``，并在每次
    网络请求前执行 ``reserve_call``。该对象只保存本次进程内的计数；真实运行还需
    把返回的计数写入attempt事件日志。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = deepcopy(config)
        self.total_calls = 0
        self.calls_by_probe: Counter[str] = Counter()
        self.reserved_cost = Decimal("0")
        self._pending: dict[int, Decimal] = {}

    def readiness_problems(self) -> list[str]:
        problems: list[str] = []
        required_nonempty = {
            "provider": self.config.get("provider"),
            "model": self.config.get("model"),
            "approved_external_data_scope": self.config.get("approved_external_data_scope"),
            "currency_limit": self.config.get("currency_limit"),
            "single_call_timeout_seconds": self.config.get("single_call_timeout_seconds"),
            "maximum_input_tokens": self.config.get("maximum_input_tokens"),
            "maximum_output_tokens": self.config.get("maximum_output_tokens"),
        }
        problems.extend(f"{key}未配置" for key, value in required_nonempty.items() if value is None)
        if self.config.get("credential_present") is not True:
            problems.append("credential_present未确认")
        if self.config.get("status") != "READY_FOR_LIVE_PROBE":
            problems.append("status不是READY_FOR_LIVE_PROBE")
        if self.config.get("maximum_total_calls", 0) <= 0:
            problems.append("maximum_total_calls必须为正数")
        if self.config.get("maximum_attempts_per_probe", 0) <= 0:
            problems.append("maximum_attempts_per_probe必须为正数")
        currency_limit = self.config.get("currency_limit")
        if currency_limit is not None:
            try:
                limit = Decimal(str(currency_limit))
                if not limit.is_finite() or limit <= 0:
                    problems.append("currency_limit必须为正数")
            except Exception:
                problems.append("currency_limit必须为有限数")
        for key in ("maximum_input_tokens", "maximum_output_tokens", "single_call_timeout_seconds"):
            value = self.config.get(key)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0):
                problems.append(f"{key}必须为正数")
        return problems

    def assert_ready(self) -> None:
        problems = self.readiness_problems()
        if problems:
            raise LiveProbeNotReady("；".join(problems))

    def reserve_call(self, probe_id: str, estimated_cost: Decimal) -> dict[str, Any]:
        """在真实请求之前占用一次调用与费用额度。"""

        self.assert_ready()
        allowed_probes = set(self.config.get("online_probe_ids", []))
        if probe_id not in allowed_probes:
            raise ProbeBudgetExceeded(f"probe_id不在在线白名单: {probe_id}")
        if not estimated_cost.is_finite() or estimated_cost < 0:
            raise ValueError("estimated_cost不能为负数")
        if self.total_calls + 1 > int(self.config["maximum_total_calls"]):
            raise ProbeBudgetExceeded("已达到总调用次数上限")
        if self.calls_by_probe[probe_id] + 1 > int(self.config["maximum_attempts_per_probe"]):
            raise ProbeBudgetExceeded(f"已达到{probe_id}单试验尝试次数上限")
        new_cost = self.reserved_cost + estimated_cost
        if new_cost > Decimal(str(self.config["currency_limit"])):
            raise ProbeBudgetExceeded("预计费用将超过货币上限")
        self.total_calls += 1
        self.calls_by_probe[probe_id] += 1
        self.reserved_cost = new_cost
        self._pending[self.total_calls] = estimated_cost
        return {
            "probe_id": probe_id,
            "total_calls": self.total_calls,
            "probe_attempt": self.calls_by_probe[probe_id],
            "reserved_cost": str(self.reserved_cost),
            "currency": self.config.get("currency"),
        }

    def settle_call(self, reservation: dict[str, Any], actual_cost: Decimal | None) -> None:
        """Settle known usage; unknown failures retain their reservation and call count."""
        identifier = reservation["total_calls"]
        if identifier not in self._pending:
            raise ValueError("调用已结算或不是当前守卫的预留")
        if actual_cost is not None and (not actual_cost.is_finite() or actual_cost < 0):
            raise ValueError("实际费用必须是非负有限数")
        estimate = self._pending.pop(identifier)
        if actual_cost is not None:
            self.reserved_cost += actual_cost - estimate
        # An unexpected provider overrun is recorded in full, never erased.
        if self.reserved_cost > Decimal(str(self.config["currency_limit"])):
            raise ProbeBudgetExceeded("已记录费用超过预算，后续调用已停止")


_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "password",
}


def redact_sensitive(value: Any) -> Any:
    """递归遮盖常见凭证字段，同时保留非敏感token计数。"""

    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value
