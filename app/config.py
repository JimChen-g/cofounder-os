"""Configuration for the Gateway."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All provider and server fields are configured via uppercase environment
    variables.  Field names are lowercase (Python convention); Pydantic
    Settings matches them case-insensitively.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Gateway identity ────────────────────────────────────────────────────
    app_name: str = "cofounder-os-gateway"
    app_version: str = "0.1.0"
    environment: str = "development"

    # ── Server ─────────────────────────────────────────────────────────────
    gateway_host: str = Field(default="127.0.0.1", validation_alias="GATEWAY_HOST")
    gateway_port: int = Field(default=9000, validation_alias="GATEWAY_PORT")
    log_level: str = "info"

    # ── Gateway / OpenAI-compatible upstream auth ───────────────────────────
    gateway_api_key: Optional[str] = Field(default=None, validation_alias="GATEWAY_API_KEY")

    # ── Qwen ───────────────────────────────────────────────────────────────
    qwen_base_url: str = Field(
        default="http://127.0.0.1:8000/v1",
        validation_alias="QWEN_BASE_URL",
    )
    qwen_api_key: Optional[str] = Field(default=None, validation_alias="QWEN_API_KEY")
    qwen_model: str = Field(default="replace-with-vllm-model-id", validation_alias="QWEN_MODEL")

    # ── Step ───────────────────────────────────────────────────────────────
    step_base_url: str = Field(
        default="https://api.stepfun.com/step_plan/v1",
        validation_alias="STEP_BASE_URL",
    )
    step_api_key: Optional[str] = Field(default=None, validation_alias="STEP_API_KEY")
    step_model: str = Field(default="step-3.7-flash", validation_alias="STEP_MODEL")

    # ── Default model preferences ──────────────────────────────────────────
    # No default model/provider — clients must specify a virtual model.

    # ── Audit ──────────────────────────────────────────────────────────────
    audit_dir: str = Field(default="data/audit", validation_alias="AUDIT_DIR")

    # ── Product API ────────────────────────────────────────────────────────
    product_data_dir: str = Field(
        default="data",
        validation_alias="PRODUCT_DATA_DIR",
    )
    product_max_artifact_bytes: int = Field(
        default=1_048_576,
        ge=1,
        le=10_485_760,
        validation_alias="PRODUCT_MAX_ARTIFACT_BYTES",
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:9000",
            "http://localhost:9000",
        ],
        validation_alias="CORS_ALLOWED_ORIGINS",
    )

    # ── Request limits ─────────────────────────────────────────────────────
    max_request_tokens: int = 128_000
    max_request_body_bytes: int = Field(
        default=12_582_912,
        ge=1_048_576,
        le=20_971_520,
        validation_alias="MAX_REQUEST_BODY_BYTES",
    )
    request_timeout_seconds: float = Field(
        default=300.0, validation_alias="REQUEST_TIMEOUT_SECONDS"
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
