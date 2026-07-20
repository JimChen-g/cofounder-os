"""Structured input and output contracts for the D09 Artifact Synthesizer."""

from __future__ import annotations

from typing import Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.finance_models import FinanceAgentResultV1
from app.domain.models import Artifact
from app.domain.product_models import ProductAgentResultV1


class ArtifactSynthesisRequest(BaseModel):
    """Validated Product and Finance inputs for deterministic synthesis."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    task_id: UUID
    objective: str = Field(min_length=1, max_length=2000)
    product: ProductAgentResultV1
    finance: FinanceAgentResultV1
    product_artifact_id: Optional[UUID] = None
    finance_artifact_id: Optional[UUID] = None
    correlation_id: Optional[str] = Field(default=None, max_length=100)


class SynthesizedArtifact(BaseModel):
    """Stable public reference to synthesized stored content."""

    model_config = ConfigDict(extra="forbid")

    logical_name: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    checksum_sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


class ArtifactSynthesisResult(BaseModel):
    """The five accepted synthesis deliverables."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    stored_artifacts: Dict[str, SynthesizedArtifact] = Field(
        min_length=5,
        max_length=5,
    )
    domain_artifacts: Dict[str, Artifact] = Field(min_length=5, max_length=5)
