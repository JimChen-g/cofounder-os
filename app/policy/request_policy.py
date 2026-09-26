"""Hard request constraints. Rankings may narrow these grants, never widen them."""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from app.policy.budget_store import BudgetStore

from app.request_constraints import RequestPolicy

from app.models import Provider
from app.provider_errors import ProviderError


class PolicyDenied(ProviderError):
    """No authorized invocation remains; fail closed without upstream traffic."""


@dataclass
class InvocationBudget:
    policy: RequestPolicy
    attempts: int = 0
    reserved_tokens: int = 0
    cloud_calls: int = 0
    started: float = 0
    store: BudgetStore | None = None
    key: str | None = None

    def __post_init__(self) -> None:
        self.started = time.monotonic()

    def reserve(self, provider: Provider, tokens: int, policy: RequestPolicy) -> float:
        effective = self.policy.intersect(policy)
        remaining = effective.timeout_seconds - (time.monotonic() - self.started)
        if not effective.allows(provider):
            raise PolicyDenied("provider_not_allowed")
        if (self.attempts >= effective.max_attempts or remaining <= 0
                or self.reserved_tokens + tokens > effective.max_total_tokens
                or (provider == Provider.STEP and self.cloud_calls >= effective.cloud_call_budget)):
            raise PolicyDenied("request_budget_exhausted")
        if self.store is not None and self.key is not None:
            remaining = min(remaining, self.store.reserve(self.key, provider, tokens, effective))
        self.attempts += 1
        self.reserved_tokens += tokens
        self.cloud_calls += int(provider == Provider.STEP)
        return remaining


active_budget: ContextVar[InvocationBudget | None] = ContextVar("request_budget", default=None)
