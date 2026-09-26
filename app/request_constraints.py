"""Immutable request constraint schema."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.models import Provider

class RequestPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    privacy: Literal["public", "internal", "restricted"] = "restricted"
    allowed_providers: frozenset[Literal["local", "step"]] = frozenset({"local"})
    permissions: frozenset[Literal["model:invoke", "cloud:invoke"]] = frozenset({"model:invoke"})
    max_attempts: int = Field(default=2, ge=0, le=20)
    max_total_tokens: int = Field(default=32768, ge=0, le=1_000_000)
    timeout_seconds: float = Field(default=300, ge=0, le=3600)
    # A zero spend budget prohibits cloud; no fabricated token-to-Credit conversion.
    cloud_call_budget: int = Field(default=0, ge=0, le=20)

    def intersect(self, other: RequestPolicy) -> RequestPolicy:
        levels = {"public": 0, "internal": 1, "restricted": 2}
        return RequestPolicy(
            privacy=max((self.privacy, other.privacy), key=levels.__getitem__),
            allowed_providers=self.allowed_providers & other.allowed_providers,
            permissions=self.permissions & other.permissions,
            max_attempts=min(self.max_attempts, other.max_attempts),
            max_total_tokens=min(self.max_total_tokens, other.max_total_tokens),
            timeout_seconds=min(self.timeout_seconds, other.timeout_seconds),
            cloud_call_budget=min(self.cloud_call_budget, other.cloud_call_budget),
        )

    def allows(self, provider: Provider) -> bool:
        channel = "local" if provider == Provider.QWEN else "step"
        return ("model:invoke" in self.permissions and channel in self.allowed_providers
                and (provider == Provider.QWEN or
                     (self.privacy != "restricted" and "cloud:invoke" in self.permissions
                      and self.cloud_call_budget > 0)))
