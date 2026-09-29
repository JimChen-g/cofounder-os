"""OpenAI-compatible provider implementation (used by Qwen and Step)."""

from __future__ import annotations

from typing import Any

import httpx

from app.models import ChatMessage, ChatResponse, Provider
from app.providers.base import BaseProvider, ProviderError


class OpenAICompatProvider(BaseProvider):
    """OpenAI-compatible Chat Completions provider.

    Used for Qwen (DashScope compatible-mode) and Step (StepFun).
    """

    def __init__(
        self,
        name: Provider,
        api_key: str | None,
        base_url: str,
        model: str,
        *,
        context_window: int | None = None,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._context_window = context_window
        self._tokenize_url = base_url.rstrip("/").removesuffix("/v1") + "/tokenize"

    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResponse:
        api_key = self._api_key
        if not api_key:
            raise ProviderError(
                f"{self.name.value} API key is not configured",
                provider=self.name,
            )

        payload = {
            "model": model or self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format is not None:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                if self._context_window is not None:
                    # Same model, messages and generation framing; never truncate evidence.
                    tokenized = await client.post(self._tokenize_url, json={
                        "model": payload["model"], "messages": payload["messages"],
                        "add_generation_prompt": True,
                    }, headers=headers)
                    if tokenized.status_code != 200:
                        raise ProviderError("context_preflight_failed", provider=self.name)
                    try:
                        count = tokenized.json()["count"]
                        if type(count) is not int or count < 0:
                            raise ValueError("invalid_count")
                    except (ValueError, KeyError, TypeError) as exc:
                        raise ProviderError("context_preflight_invalid", provider=self.name) from exc
                    available = self._context_window - count - 64
                    # Retain room for a complete structured review, not a tiny fragment.
                    if available < min(max_tokens, 2000):
                        raise ProviderError("context_capacity_insufficient", provider=self.name)
                    payload["max_tokens"] = min(max_tokens, available)
                resp = await client.post(self._base_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name.value} transport failed", provider=self.name) from exc

        if resp.status_code != 200:
            raise ProviderError(
                f"{self.name.value} returned HTTP {resp.status_code}",
                provider=self.name,
            )

        try:
            data: dict[str, Any] = resp.json()
            if not isinstance(data, dict):
                raise ValueError("response_not_object")
        except ValueError as exc:
            raise ProviderError("upstream_invalid_json", provider=self.name) from exc

        # Validate upstream response structure
        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise ProviderError(
                f"{self.name.value} returned response with no choices",
                provider=self.name,
            )

        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("upstream_invalid_choice", provider=self.name)
        message = choice.get("message")
        if not message or not isinstance(message, dict):
            raise ProviderError(
                f"{self.name.value} returned response with no message object",
                provider=self.name,
            )

        usage = data.get("usage", {})

        # Preserve valid upstream message fields; never copy reasoning_content
        upstream_content = message.get("content")
        tool_calls = message.get("tool_calls")
        function_call = message.get("function_call")
        refusal = message.get("refusal")

        return self._build_response(
            provider=self.name,
            model=model or self._model,  # virtual model name (caller decides)
            content=upstream_content,
            finish_reason=choice.get("finish_reason", "stop"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            selected_upstream_model=data.get("model", model or self._model),
            tool_calls=tool_calls,
            function_call=function_call,
            refusal=refusal,
        )

    async def health(self) -> tuple[str, float | None]:
        api_key = self._api_key
        if not api_key:
            return "unavailable", None

        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            import time

            async with httpx.AsyncClient(timeout=10.0) as client:
                t0 = time.perf_counter()
                resp = await client.get(
                    self._base_url.replace("/chat/completions", "/models"), headers=headers
                )
                latency_ms = (time.perf_counter() - t0) * 1000

            if resp.status_code == 200:
                return "healthy", latency_ms
            return "degraded", latency_ms
        except Exception:
            return "unavailable", None
