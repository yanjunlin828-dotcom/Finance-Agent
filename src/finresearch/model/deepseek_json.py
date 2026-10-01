"""通过LangChain调用DeepSeek，并把响应限制为Pydantic JSON契约。"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, TypeVar, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .probe_guard import LiveProbeGuard, redact_sensitive

T = TypeVar("T", bound=BaseModel)


class ModelCallFailure(RuntimeError):
    """A failed attempt, with public response/usage audit available to the caller."""

    def __init__(self, code: str, audits: list[dict[str, Any]]) -> None:
        super().__init__(code)
        self.code = code
        self.audits = audits


class DeepSeekJsonClient:
    """供应商适配器；调用前先由共享预算守卫预留额度。"""

    def __init__(self, config: dict[str, Any], api_key: str, guard: LiveProbeGuard,
                 audit_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.config = config
        self.guard = guard
        self.audit_sink = audit_sink
        self.attempt_audits: list[dict[str, Any]] = []
        self._secret = api_key
        self._model = ChatOpenAI(
            model=config["model"],
            api_key=api_key,
            base_url=config["base_url"],
            timeout=float(config["single_call_timeout_seconds"]),
            max_retries=0,
            max_completion_tokens=int(config["maximum_output_tokens"]),
            temperature=0,
            extra_body={"thinking": {"type": config["thinking_mode"]}},
        )

    def invoke_json(
        self,
        call_id: str,
        output_type: type[T],
        system_text: str,
        user_text: str,
    ) -> tuple[T, dict[str, Any]]:
        """发起一次受预算约束的JSON调用，返回解析对象和脱敏审计记录。"""

        schema = json.dumps(output_type.model_json_schema(), ensure_ascii=False)
        messages = [
                SystemMessage(
                    content=(
                        f"{system_text}\n只输出一个JSON对象，不要Markdown、解释或额外字段。"
                        f"JSON必须满足以下schema：{schema}"
                    )
                ),
                HumanMessage(content=user_text),
            ]
        audits: list[dict[str, Any]] = []
        for _ in range(int(self.config["maximum_attempts_per_probe"])):
            response, audit = self.invoke_messages(call_id, messages,
                {"response_format": {"type": "json_object"}}, emit_audit=False)
            try:
                if audit["response_metadata"].get("finish_reason") == "length":
                    audit["status"] = "OUTPUT_LIMIT"
                    self._record(audit)
                    raise ModelCallFailure("OUTPUT_LIMIT", [*audits, audit])
                if not isinstance(response.content, str) or not response.content.strip():
                    raise ValueError("empty JSON response")
                parsed = output_type.model_validate_json(response.content)
            except ModelCallFailure:
                raise
            except ValueError:
                audit["status"] = "INVALID_STRUCTURED_OUTPUT"
                audits.append(audit)
                self._record(audit)
                continue
            audit["status"] = "PARSED"
            self._record(audit)
            return parsed, audit
        raise ModelCallFailure("INVALID_STRUCTURED_OUTPUT", audits)

    def _record(self, audit: dict[str, Any]) -> None:
        # Remove the actual credential even if a provider echoes it in plain text.
        def scrub(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace(self._secret, "[REDACTED]") if self._secret else value
            if isinstance(value, dict):
                return {key: scrub(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [scrub(item) for item in value]
            return value
        safe = redact_sensitive(scrub(audit))
        audit.clear()
        audit.update(safe)
        self.attempt_audits.append(audit)
        if self.audit_sink is not None:
            self.audit_sink(audit)

    def invoke_messages(self, call_id: str, messages: list[Any], bind_options: dict[str, Any],
                        *, emit_audit: bool = True) -> tuple[Any, dict[str, Any]]:
        """Shared guarded transport for JSON and the S0 read-only tool probe.

        Without a provider tokenizer, cap the UTF-8 byte upper bound including
        schema/tool definitions and message framing. This is deliberately more
        restrictive than a token estimate; no local tokenizer is labelled exact.
        """
        serialized = json.dumps({"messages": [m.model_dump(mode="json") for m in messages],
                                 "options": bind_options}, ensure_ascii=False)
        input_bound = len(serialized.encode("utf-8"))
        if input_bound > int(self.config["maximum_input_tokens"]):
            raise ModelCallFailure("INPUT_BUDGET_EXCEEDED", [])
        maximum_cost = conservative_cost({"model_input_tokens": input_bound,
                                           "model_output_tokens": int(self.config["maximum_output_tokens"])}, self.config)
        reserve = max(Decimal(str(self.config["estimated_reservation_per_call"])), Decimal(maximum_cost))
        reservation = self.guard.reserve_call(call_id, reserve)
        audit = {"call_id": call_id, "content": None, "tool_calls": [],
                 "usage": {"model_input_tokens": None, "model_output_tokens": None, "total_tokens": None},
                 "response_metadata": {}, "budget_reservation": reservation,
                 "estimated_cost_usd": None, "input_utf8_byte_upper_bound": input_bound,
                 "input_counting_policy": "UTF8_BYTE_UPPER_BOUND", "status": "TRANSPORT_FAILED"}
        try:
            response = self._model.bind(**bind_options).invoke(messages)
        except Exception as exc:
            audit["exception_type"] = type(exc).__name__
            self.guard.settle_call(reservation, None)
            self._record(audit)
            raise ModelCallFailure("TRANSPORT_FAILED", [audit]) from None
        usage = getattr(response, "usage_metadata", None) or {}
        audit.update(content=response.content, tool_calls=getattr(response, "tool_calls", []),
                     response_metadata=getattr(response, "response_metadata", {}), status="RECEIVED")
        audit["usage"] = {"model_input_tokens": usage.get("input_tokens"),
                          "model_output_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens")}
        audit["estimated_cost_usd"] = conservative_cost(audit["usage"], self.config)
        try:
            self.guard.settle_call(reservation, Decimal(audit["estimated_cost_usd"]) if audit["estimated_cost_usd"] is not None else None)
        except RuntimeError:
            audit["status"] = "COST_BUDGET_EXCEEDED"
            self._record(audit)
            raise ModelCallFailure("COST_BUDGET_EXCEEDED", [audit]) from None
        if emit_audit:
            self._record(audit)
        return response, audit


def conservative_cost(usage: dict[str, int | None], config: dict[str, Any]) -> str | None:
    """按配置中的峰值价格保守估算一次调用费用。"""

    input_tokens = usage.get("model_input_tokens")
    output_tokens = usage.get("model_output_tokens")
    if input_tokens is None or output_tokens is None:
        return None
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in (input_tokens, output_tokens)):
        return None
    pricing = config["pricing_assumption"]
    cost = Decimal(input_tokens) * Decimal(pricing["input_usd_per_million_tokens"]) / Decimal(1_000_000)
    cost += Decimal(output_tokens) * Decimal(pricing["output_usd_per_million_tokens"]) / Decimal(1_000_000)
    if not cost.is_finite() or cost < 0:
        raise ValueError("费用配置必须非负且有限")
    return str(cost.quantize(Decimal("0.00000001")))
