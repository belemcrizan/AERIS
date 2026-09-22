"""Model clients for the one live runtime adapter.

``OpenAIChatModel`` talks to any OpenAI-compatible ``/chat/completions``
endpoint with the standard library only, so AERIS gains no dependency and
CI never needs a key. It is constructed only when the caller opts in
(``AERIS_LIVE=1`` plus ``OPENAI_API_KEY``); see LIVE_AGENT.md.

Model output is untrusted. Token counts come from the provider's ``usage``
block when present (RUNTIME_REPORTED) and are otherwise estimated.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str = ""


class ModelResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""
    model_version: str | None = None
    usage_reported: bool = False
    error: str | None = None


class ModelPricing(BaseModel):
    """USD per million tokens. Estimates, not invoices."""

    input_per_million: float = 0.15
    output_per_million: float = 0.60

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_million + output_tokens * self.output_per_million
        ) / 1_000_000


@runtime_checkable
class ChatModel(Protocol):
    name: str
    stochastic: bool
    pricing: ModelPricing

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        temperature: float,
        seed: int | None,
    ) -> ModelResponse: ...


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return max(1, len(text) // 4)


class OpenAIChatModel:
    """Minimal OpenAI-compatible chat client (function calling)."""

    stochastic = True

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        pricing: ModelPricing | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required for the live model")
        self.name = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.pricing = pricing or ModelPricing()
        self._timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> OpenAIChatModel:
        pricing = ModelPricing(
            input_per_million=float(os.getenv("AERIS_LIVE_PRICE_IN", "0.15")),
            output_per_million=float(os.getenv("AERIS_LIVE_PRICE_OUT", "0.60")),
        )
        return cls(
            os.getenv("AERIS_LIVE_MODEL", "gpt-4o-mini"),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            pricing=pricing,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        temperature: float,
        seed: int | None,
    ) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": messages,
            "temperature": temperature,
        }
        if seed is not None:
            body["seed"] = seed
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        started = time.perf_counter()
        try:
            data = await asyncio.to_thread(self._post, body)
        except Exception as exc:  # network, HTTP, JSON: surfaced as a model error, not a crash
            return ModelResponse(
                error=f"model_error: {exc}",
                latency_ms=(time.perf_counter() - started) * 1000,
                model=self.name,
            )
        latency = (time.perf_counter() - started) * 1000
        choice = (data.get("choices") or [{}])[0].get("message", {})
        calls = []
        for raw in choice.get("tool_calls") or []:
            function = raw.get("function", {})
            arguments_text = function.get("arguments") or "{}"
            try:
                arguments = json.loads(arguments_text)
            except json.JSONDecodeError:
                arguments = {}
            calls.append(
                ToolCall(
                    call_id=raw.get("id", ""),
                    name=function.get("name", ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                    raw_arguments=arguments_text,
                )
            )
        usage = data.get("usage") or {}
        return ModelResponse(
            content=choice.get("content"),
            tool_calls=calls,
            input_tokens=int(usage.get("prompt_tokens") or estimate_tokens(messages)),
            output_tokens=int(usage.get("completion_tokens") or estimate_tokens(choice)),
            latency_ms=latency,
            model=self.name,
            model_version=data.get("model") or data.get("system_fingerprint"),
            usage_reported=bool(usage),
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def live_enabled() -> bool:
    return os.getenv("AERIS_LIVE") == "1" and bool(os.getenv("OPENAI_API_KEY"))
