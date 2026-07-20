"""Request router — selects and invokes providers."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from app.models import ChatRequest, ChatResponse, CofounderOSMetadata, Provider
from app.providers.registry import get_registry
from app.audit.logger import get_audit_logger
from app.config import get_settings

# Virtual model → (provider enum, upstream model name) mapping
_VIRTUAL_MODELS: dict[str, tuple[Provider, str]] = {
    "cofounder-auto": (Provider.QWEN, ""),   # upstream model chosen at routing time
    "cofounder-qwen": (Provider.QWEN, ""),
    "cofounder-step": (Provider.STEP, ""),
}

# Routing reason for explicit virtual model requests
_MODEL_ROUTING_REASONS: dict[str, str] = {
    "cofounder-qwen": "forced_local",
    "cofounder-step": "forced_deep",
}

# Fallback routing reason when preferred provider fails
# Key = provider that was actually used after fallback
_FALLBACK_ROUTING_REASONS: dict[Provider, str] = {
    Provider.QWEN: "fallback_local",   # preferred failed, stayed with local
    Provider.STEP: "fallback_deep",    # preferred failed, fell back to deep
}

# Provider display names for metadata (strip "cofounder-" prefix)
_PROVIDER_DISPLAY_NAMES: dict[Provider, str] = {
    Provider.QWEN: "qwen",
    Provider.STEP: "step",
}


def _get_configured_upstream_model(provider: Provider) -> str:
    """Return the configured upstream model name for a provider from settings."""
    settings = get_settings()
    if provider == Provider.QWEN:
        return settings.qwen_model
    return settings.step_model


def _build_cofounder_os_metadata(
    request_id: str,
    used_provider: Provider,
    preferred_provider: Provider,
    virtual_name: str,
    latency_ms: float,
) -> CofounderOSMetadata:
    """Build the cofounder_os metadata object from the routing decision."""
    settings = get_settings()

    # Determine routing reason from the actual routing decision
    if virtual_name in _MODEL_ROUTING_REASONS:
        # Explicit model selection (cofounder-qwen or cofounder-step)
        routing_reason = _MODEL_ROUTING_REASONS[virtual_name]
    elif used_provider == preferred_provider:
        # Auto mode — preferred provider handled the request
        routing_reason = "local_default"
    else:
        # Auto mode — fell back to the other provider
        routing_reason = _FALLBACK_ROUTING_REASONS.get(
            used_provider, "fallback_unknown"
        )

    return CofounderOSMetadata(
        request_id=request_id,
        selected_provider=_PROVIDER_DISPLAY_NAMES[used_provider],
        selected_upstream_model=_get_configured_upstream_model(used_provider),
        routing_reason=routing_reason,
        latency_ms=round(latency_ms, 2),
        version=settings.app_version,
    )


async def route_chat(
    request: ChatRequest,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> ChatResponse:
    """Route a chat completion request to the best available provider.

    Virtual model names (cofounder-auto, cofounder-qwen, cofounder-step) are
    translated to the appropriate upstream provider.  Upstream model names
    are never exposed to clients.
    """
    registry = get_registry()
    audit = get_audit_logger()

    # Use provided request_id or generate one
    if request_id is None:
        request_id = f"req-{uuid.uuid4().hex[:16]}"

    # Resolve virtual model to provider and upstream model
    virtual_name = request.model or "cofounder-auto"
    if virtual_name not in _VIRTUAL_MODELS:
        raise ValueError(
            f"Unknown model '{virtual_name}'. "
            f"Valid models: cofounder-auto, cofounder-qwen, cofounder-step"
        )

    target_provider, upstream_model = _VIRTUAL_MODELS[virtual_name]
    preferred = target_provider
    resolved_upstream_model = upstream_model

    # ── Pre-compute audit fields from the request ────────────────────────
    message_count = len(request.messages)
    tool_count = len(getattr(request, "tools", None) or [])
    try:
        prompt_canonical = json.dumps(
            [m.model_dump(mode="json") for m in request.messages],
            sort_keys=True,
            separators=(",", ":"),
        )
        prompt_sha256 = hashlib.sha256(prompt_canonical.encode("utf-8")).hexdigest()
    except Exception:
        prompt_sha256 = None

    # Canonical endpoint identifier
    _ENDPOINT = "/v1/chat/completions"

    t0 = time.perf_counter()
    try:
        response, used_provider = await registry.complete_with_fallback(
            preferred=preferred,
            model=resolved_upstream_model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens or 1024,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        # Override the response model with the virtual model name
        # and record the actual upstream model used
        response.model = virtual_name
        response.selected_upstream_model = response.selected_upstream_model or response.model

        # Attach cofounder_os metadata from the routing decision
        response.cofounder_os = _build_cofounder_os_metadata(
            request_id=request_id,
            used_provider=used_provider,
            preferred_provider=preferred,
            virtual_name=virtual_name,
            latency_ms=latency_ms,
        )

        # ── Audit: use the same routing values that appear in the response ──
        meta = response.cofounder_os
        audit.log_request(
            request_id=request_id,
            provider=used_provider.value,
            model=virtual_name,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            latency_ms=latency_ms,
            status="success",
            user_agent=user_agent,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            endpoint=_ENDPOINT,
            requested_virtual_model=virtual_name,
            selected_provider=meta.selected_provider if meta else None,
            selected_upstream_model=meta.selected_upstream_model if meta else None,
            routing_reason=meta.routing_reason if meta else None,
            message_count=message_count,
            tool_count=tool_count,
            prompt_sha256=prompt_sha256,
        )

        return response

    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        # Routing was not completed — serialize routing fields as null
        audit.log_request(
            request_id=request_id,
            provider=preferred.value,
            model=virtual_name,
            latency_ms=latency_ms,
            status="error",
            error=str(exc),
            user_agent=user_agent,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            endpoint=_ENDPOINT,
            requested_virtual_model=virtual_name,
            selected_provider=None,
            selected_upstream_model=None,
            routing_reason=None,
            message_count=message_count,
            tool_count=tool_count,
            prompt_sha256=prompt_sha256,
        )
        raise
