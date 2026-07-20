"""Deterministic risk, permission, approval, and denial rules (D08)."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal

from app.policy.models import (
    PolicyAction,
    PolicyDecision,
    PolicyDisposition,
    RiskLevel,
    ToolPermission,
)


_FORK_BOMB = re.compile(
    r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    re.I,
)
_SHELL_SEPARATORS = frozenset({";", "&", "|", "&&", "||"})
_SHELL_WRAPPERS = frozenset({"builtin", "busybox", "command", "nohup"})
_SHELL_INTERPRETERS = frozenset({"bash", "dash", "ksh", "sh", "zsh"})
_SUDO_OPTIONS_WITH_VALUE = frozenset({
    "--chdir",
    "--close-from",
    "--group",
    "--host",
    "--prompt",
    "--role",
    "--type",
    "--user",
    "-C",
    "-D",
    "-R",
    "-T",
    "-g",
    "-h",
    "-p",
    "-r",
    "-t",
    "-u",
})


@dataclass(frozen=True)
class _MatchedRule:
    rule_id: str
    reason: str


def _executable_name(token: str) -> str:
    """Return a case-normalized executable basename."""
    return token.rsplit("/", 1)[-1].lower()


def _split_shell_segments(command: str) -> list[list[str]]:
    """Tokenize shell commands while preserving command boundaries."""
    lexer = shlex.shlex(
        command.replace("\n", " ; "),
        posix=True,
        punctuation_chars=";&|",
    )
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = []
    current: list[str] = []
    for token in lexer:
        if token in _SHELL_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _unwrap_shell_command(tokens: list[str]) -> list[str]:
    """Remove well-known execution wrappers without executing anything."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        name = _executable_name(token)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            index += 1
            continue
        if name in _SHELL_WRAPPERS:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            continue
        if name == "env":
            index += 1
            while index < len(tokens):
                value = tokens[index]
                if value.startswith("-") or re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*=.*",
                    value,
                ):
                    index += 1
                    continue
                break
            continue
        if name == "sudo":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                option = tokens[index]
                index += 1
                if option in _SUDO_OPTIONS_WITH_VALUE and index < len(tokens):
                    index += 1
            continue
        break
    return tokens[index:]


def _dangerous_segment(
    tokens: list[str],
    *,
    depth: int = 0,
) -> _MatchedRule | None:
    """Classify one tokenized shell segment."""
    command = _unwrap_shell_command(tokens)
    if not command:
        return None
    executable = _executable_name(command[0])
    arguments = command[1:]

    if executable in _SHELL_INTERPRETERS and "-c" in arguments and depth < 2:
        command_index = arguments.index("-c")
        nested = " ".join(arguments[command_index + 1 :])
        if nested:
            return _dangerous_command(nested, depth=depth + 1)

    if executable in {"eval", "exec"} and arguments and depth < 2:
        return _dangerous_command(" ".join(arguments), depth=depth + 1)

    if executable == "rm":
        short_options = [
            value[1:]
            for value in arguments
            if value.startswith("-") and not value.startswith("--")
        ]
        recursive = "--recursive" in arguments or any(
            "r" in value or "R" in value for value in short_options
        )
        force = "--force" in arguments or any(
            "f" in value for value in short_options
        )
        if recursive and force:
            return _MatchedRule(
                "deny.rm_recursive_force",
                "Recursive forced deletion is prohibited.",
            )

    if (
        executable in {"fdisk", "parted"}
        or executable == "mkfs"
        or executable.startswith("mkfs.")
        or (
            executable == "dd"
            and any(value.startswith("of=/dev/") for value in arguments)
        )
    ):
        return _MatchedRule(
            "deny.disk_device_write",
            "Raw disk formatting or device writes are prohibited.",
        )

    if executable in {"halt", "poweroff", "reboot", "shutdown"}:
        return _MatchedRule(
            "deny.host_power_control",
            "Host power-control commands are prohibited.",
        )

    if executable == "git" and "reset" in arguments and "--hard" in arguments:
        return _MatchedRule(
            "deny.destructive_git_reset",
            "Destructive Git reset is prohibited.",
        )
    return None


def _dangerous_command(
    command: str,
    *,
    depth: int = 0,
) -> _MatchedRule | None:
    """Return the first dangerous shell rule matched after normalization."""
    if _FORK_BOMB.search(command):
        return _MatchedRule(
            "deny.fork_bomb",
            "Fork-bomb execution is prohibited.",
        )
    try:
        segments = _split_shell_segments(command)
    except ValueError:
        return _MatchedRule(
            "deny.unparseable_shell_command",
            "Unparseable shell commands are prohibited.",
        )
    for segment in segments:
        matched = _dangerous_segment(segment, depth=depth)
        if matched is not None:
            return matched
    return None


class DeterministicPolicyGate:
    """Evaluate normalized facts without model calls or prompt interpretation."""

    def __init__(self, *, material_budget_threshold: float = 1000.0) -> None:
        if material_budget_threshold < 0:
            raise ValueError("material_budget_threshold must be non-negative")
        self.material_budget_threshold = material_budget_threshold

    def evaluate(self, action: PolicyAction) -> PolicyDecision:
        denial = self._denial_rule(action)
        if denial is not None:
            return PolicyDecision(
                risk_level=RiskLevel.CRITICAL,
                tool_permission=ToolPermission.BLOCKED,
                disposition=PolicyDisposition.DENY,
                approval_required=False,
                irreversible_action=action.irreversible
                or action.operation == "delete",
                rule_ids=[denial.rule_id],
                reasons=[denial.reason],
            )

        approvals = self._approval_rules(action)
        if approvals:
            reviewer: Literal["founder", "security", "finance"] = "founder"
            if any(rule.rule_id == "approval.material_budget" for rule in approvals):
                reviewer = "finance"
            if any(rule.rule_id == "approval.private_data_access" for rule in approvals):
                reviewer = "security"
            return PolicyDecision(
                risk_level=RiskLevel.HIGH,
                tool_permission=ToolPermission.GUARDED,
                disposition=PolicyDisposition.REQUIRE_APPROVAL,
                approval_required=True,
                reviewer_required=reviewer,
                irreversible_action=action.irreversible
                or action.operation in {"delete", "transact"},
                rule_ids=[rule.rule_id for rule in approvals],
                reasons=[rule.reason for rule in approvals],
            )

        if action.operation == "read":
            return PolicyDecision(
                risk_level=RiskLevel.LOW,
                tool_permission=ToolPermission.READ_ONLY,
                disposition=PolicyDisposition.ALLOW,
                approval_required=False,
                irreversible_action=False,
                rule_ids=["allow.read_only"],
                reasons=["Read-only action has no declared external side effect."],
            )

        return PolicyDecision(
            risk_level=RiskLevel.MODERATE,
            tool_permission=ToolPermission.GUARDED,
            disposition=PolicyDisposition.ALLOW,
            approval_required=False,
            irreversible_action=False,
            rule_ids=["allow.local_reversible"],
            reasons=["Local reversible action is permitted within declared scope."],
        )

    @staticmethod
    def _denial_rule(action: PolicyAction) -> _MatchedRule | None:
        if action.command:
            matched = _dangerous_command(action.command)
            if matched is not None:
                return matched

        if action.operation == "upload" and action.private_data:
            return _MatchedRule(
                "deny.private_data_upload",
                "Private data must not be uploaded to an external destination.",
            )

        if action.tool_name in {
            "credential-export",
            "raw-provider-call",
            "public-secret-publish",
        }:
            return _MatchedRule(
                "deny.blocked_tool",
                f"Tool {action.tool_name!r} is blocked by the frozen boundary.",
            )
        return None

    def _approval_rules(self, action: PolicyAction) -> list[_MatchedRule]:
        rules: list[_MatchedRule] = []
        if action.operation == "execute" and action.command:
            rules.append(_MatchedRule(
                "approval.command_execute",
                "Unblocked command execution requires explicit approval.",
            ))
        if action.external_write:
            rules.append(_MatchedRule(
                "approval.external_write",
                "External writes require explicit human approval.",
            ))
        if action.production_change:
            rules.append(_MatchedRule(
                "approval.production_change",
                "Production configuration changes require explicit approval.",
            ))
        if action.irreversible or action.operation in {"delete", "transact"}:
            rules.append(_MatchedRule(
                "approval.irreversible",
                "Irreversible or transactional actions require explicit approval.",
            ))
        if (
            action.material_budget_amount is not None
            and action.material_budget_amount >= self.material_budget_threshold
        ):
            rules.append(_MatchedRule(
                "approval.material_budget",
                "Material budget commitments require finance approval.",
            ))
        if action.private_data:
            rules.append(_MatchedRule(
                "approval.private_data_access",
                "Private-data access requires security approval.",
            ))
        if action.operation in {"message", "upload"}:
            rules.append(_MatchedRule(
                "approval.external_delivery",
                "External delivery requires explicit approval.",
            ))
        return rules
