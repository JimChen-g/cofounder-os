"""Shared Pydantic models for requests and responses."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Provider(str, Enum):
    """Supported AI providers — virtual model identifiers exposed to clients."""

    QWEN = "cofounder-qwen"
    STEP = "cofounder-step"


class Role(str, Enum):
    """Chat message role."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    """A single message in a conversation.

    Content may be None for assistant messages (e.g. tool-call-only responses,
    reasoning models, or responses terminated before final text).
    """

    role: Role
    content: str | None = None


class ChatRequest(BaseModel):
    """Incoming chat completion request."""

    provider: Optional[Provider] = None
    model: Optional[str] = None
    messages: list[ChatMessage] = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=1024, ge=1, le=128_000)
    stream: bool = False
    privacy: Literal["public", "internal", "restricted"] = "restricted"
    allowed_providers: tuple[Literal["local", "step"], ...] = ("local",)
    policy: dict[str, Any] = Field(default_factory=dict)


class ChatChoice(BaseModel):
    """A single completion choice."""

    index: int
    message: ChatMessage
    finish_reason: str


class Usage(BaseModel):
    """Token usage statistics."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    """Standardised chat completion response."""

    id: str
    provider: Provider
    model: str
    selected_upstream_model: Optional[str] = None
    choices: list[ChatChoice]
    usage: Usage
    cofounder_os: Optional[CofounderOSMetadata] = None


class CofounderOSMetadata(BaseModel):
    """Gateway routing metadata attached to every successful response."""

    request_id: str
    selected_provider: str
    selected_upstream_model: str
    routing_reason: str
    latency_ms: float
    version: str


class ModelInfo(BaseModel):
    """Model listing entry — virtual model exposed to clients."""

    id: str
    provider: Provider
    owned_by: str


class ProviderHealth(BaseModel):
    """Health status for a single provider."""

    provider: str
    status: str  # "healthy" | "degraded" | "unavailable"
    latency_ms: Optional[float] = None
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    """Overall gateway health response."""

    status: str
    version: str
    providers: list[ProviderHealth]


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str
    request_id: Optional[str] = None
