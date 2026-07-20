"""D08 deterministic Policy Gate tests."""

from __future__ import annotations

import pytest

from app.policy import (
    DeterministicPolicyGate,
    PolicyAction,
    PolicyDisposition,
    RiskLevel,
    ToolPermission,
)


def _action(**overrides) -> PolicyAction:
    values = {
        "actor": "product-agent",
        "operation": "read",
        "tool_name": "artifact-read",
    }
    values.update(overrides)
    return PolicyAction(**values)


def test_read_only_action_is_allowed():
    decision = DeterministicPolicyGate().evaluate(_action())
    assert decision.disposition == PolicyDisposition.ALLOW
    assert decision.tool_permission == ToolPermission.READ_ONLY
    assert decision.risk_level == RiskLevel.LOW
    assert decision.approval_required is False


@pytest.mark.parametrize(
    "command,rule_id",
    [
        ("rm -rf ./data", "deny.rm_recursive_force"),
        ("rm -r -f ./data", "deny.rm_recursive_force"),
        ("rm -fr ./data", "deny.rm_recursive_force"),
        ("rm --recursive --force ./data", "deny.rm_recursive_force"),
        ("command rm -rf ./data", "deny.rm_recursive_force"),
        ("sudo /bin/rm -rf ./data", "deny.rm_recursive_force"),
        ("sudo --user root /bin/rm -r -f ./data", "deny.rm_recursive_force"),
        ("bash -c 'rm -rf ./data'", "deny.rm_recursive_force"),
        ("sudo mkfs.ext4 /dev/sda", "deny.disk_device_write"),
        ("shutdown -h now", "deny.host_power_control"),
        ("sudo /sbin/shutdown -h now", "deny.host_power_control"),
        ("git reset --hard HEAD~1", "deny.destructive_git_reset"),
        ("git -C . reset --hard HEAD~1", "deny.destructive_git_reset"),
        (":(){ :|:& };:", "deny.fork_bomb"),
    ],
)
def test_dangerous_commands_are_denied(command, rule_id):
    decision = DeterministicPolicyGate().evaluate(_action(
        operation="execute",
        tool_name="shell",
        command=command,
    ))
    assert decision.disposition == PolicyDisposition.DENY
    assert decision.tool_permission == ToolPermission.BLOCKED
    assert decision.rule_ids == [rule_id]
    assert decision.approval_required is False


def test_private_data_upload_is_denied_not_approvable():
    decision = DeterministicPolicyGate().evaluate(_action(
        operation="upload",
        tool_name="external-upload",
        private_data=True,
        external_write=True,
    ))
    assert decision.disposition == PolicyDisposition.DENY
    assert decision.rule_ids == ["deny.private_data_upload"]


def test_external_write_requires_approval():
    decision = DeterministicPolicyGate().evaluate(_action(
        operation="write",
        tool_name="github-write",
        external_write=True,
    ))
    assert decision.disposition == PolicyDisposition.REQUIRE_APPROVAL
    assert decision.tool_permission == ToolPermission.GUARDED
    assert decision.reviewer_required == "founder"


def test_unrecognized_command_execution_fails_closed_to_approval():
    decision = DeterministicPolicyGate().evaluate(_action(
        operation="execute",
        tool_name="shell",
        command="python -c 'print(1)'",
    ))
    assert decision.disposition == PolicyDisposition.REQUIRE_APPROVAL
    assert decision.rule_ids == ["approval.command_execute"]
    assert decision.reviewer_required == "founder"


def test_production_change_and_irreversible_action_accumulate_rules():
    decision = DeterministicPolicyGate().evaluate(_action(
        operation="configure",
        tool_name="service-config",
        production_change=True,
        irreversible=True,
    ))
    assert decision.disposition == PolicyDisposition.REQUIRE_APPROVAL
    assert decision.irreversible_action is True
    assert decision.rule_ids == [
        "approval.production_change",
        "approval.irreversible",
    ]


def test_material_budget_uses_configured_threshold_and_finance_reviewer():
    gate = DeterministicPolicyGate(material_budget_threshold=500)
    below = gate.evaluate(_action(
        operation="write",
        tool_name="budget-draft",
        material_budget_amount=499,
    ))
    at = gate.evaluate(_action(
        operation="transact",
        tool_name="budget-commit",
        material_budget_amount=500,
    ))
    assert below.disposition == PolicyDisposition.ALLOW
    assert at.disposition == PolicyDisposition.REQUIRE_APPROVAL
    assert at.reviewer_required == "finance"
    assert "approval.material_budget" in at.rule_ids


def test_policy_decision_is_deterministic():
    gate = DeterministicPolicyGate()
    action = _action(
        operation="message",
        tool_name="email",
        external_write=True,
    )
    first = gate.evaluate(action).model_dump(mode="json")
    second = gate.evaluate(action).model_dump(mode="json")
    assert first == second


def test_blocked_tool_is_denied():
    decision = DeterministicPolicyGate().evaluate(_action(
        operation="execute",
        tool_name="raw-provider-call",
    ))
    assert decision.disposition == PolicyDisposition.DENY
    assert decision.rule_ids == ["deny.blocked_tool"]
