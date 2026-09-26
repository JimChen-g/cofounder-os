"""Provider failures shared by hard policy and transports."""
from app.models import Provider

class ProviderError(Exception):
    """Raised when a provider call fails."""

    def __init__(self, message: str, provider: Provider | None = None) -> None:
        super().__init__(message)
        self.provider = provider
