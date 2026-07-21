"""Tests for the explicit D05 agent registry."""

import pytest

from app.agents import (
    EXECUTIVE_AGENT_ID,
    AgentDefinition,
    AgentRegistry,
    DuplicateAgentError,
    UnknownAgentError,
)


def test_default_registry_contains_bounded_agent_set():
    registry = AgentRegistry()

    assert registry.ids() == (
        "executive-orchestrator",
        "research-agent",
        "product-agent",
        "finance-agent",
        "legal-agent",
        "operations-agent",
        "evidence-extractor",
        "engineering-agent",
        "risk-agent",
        "artifact-synthesizer",
        "verifier",
        "release-agent",
    )
    assert registry.get(EXECUTIVE_AGENT_ID).can_plan is True
    # Only agents with a concrete Controller adapter are executable.
    executable = [
        definition.agent_id
        for definition in registry.list()
        if definition.can_execute
    ]
    assert executable == [
        "executive-orchestrator",
        "product-agent",
        "finance-agent",
        "evidence-extractor",
        "engineering-agent",
        "risk-agent",
        "artifact-synthesizer",
        "verifier",
        "release-agent",
    ]


def test_unknown_agent_is_rejected():
    registry = AgentRegistry()

    with pytest.raises(UnknownAgentError):
        registry.require_executable("invented-agent")


def test_duplicate_agent_ids_are_rejected():
    duplicate = AgentDefinition(
        agent_id=EXECUTIVE_AGENT_ID,
        display_name="Duplicate",
        purpose="Duplicate definition.",
        capabilities=("duplicate",),
        can_plan=True,
    )

    with pytest.raises(DuplicateAgentError):
        AgentRegistry([duplicate, duplicate])
