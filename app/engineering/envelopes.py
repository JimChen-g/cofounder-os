"""Frozen T02 interchange factories. Git commits and diff digests are distinct.

These envelopes report task/execution/review evidence, never delivery approval.
Callers provide actual Git commit identities; no digest is truncated into a SHA.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from importlib.resources import files
from typing import Any, Literal
from uuid import UUID, uuid4


Kind = Literal["task", "execution_result", "review"]


def _check(value: Any, rule: dict[str, Any]) -> None:
    """Validate the closed subset used by the unchanged frozen schema."""
    if "anyOf" in rule:
        for option in rule["anyOf"]:
            try:
                _check(value, option)
                return
            except ValueError:
                pass
        raise ValueError("no_anyof_match")
    if "const" in rule and value != rule["const"]:
        raise ValueError("constant_mismatch")
    if "enum" in rule and value not in rule["enum"]:
        raise ValueError("enum_mismatch")
    kind = rule.get("type")
    types: dict[str, Any] = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    if kind and (
        not isinstance(value, types[kind])
        or (kind in {"integer", "number"} and isinstance(value, bool))
    ):
        raise ValueError("type_mismatch")
    if isinstance(value, dict):
        if set(rule.get("required", [])) - value.keys():
            raise ValueError("missing_fields")
        props = rule.get("properties", {})
        if rule.get("additionalProperties") is False and value.keys() - props.keys():
            raise ValueError("extra_fields")
        for key in value.keys() & props.keys():
            _check(value[key], props[key])
    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0):
            raise ValueError("too_few_items")
        if rule.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(
            value
        ):
            raise ValueError("duplicate_items")
        for item in value:
            _check(item, rule.get("items", {}))
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0):
            raise ValueError("short_string")
        if "pattern" in rule and re.search(rule["pattern"], value) is None:
            raise ValueError("pattern_mismatch")
        if rule.get("format") == "uuid":
            UUID(value)
        if rule.get("format") == "date-time":
            if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("timezone_required")
    if type(value) in (int, float):
        if value < rule.get("minimum", float("-inf")) or value > rule.get("maximum", float("inf")):
            raise ValueError("numeric_bounds")


def validate_envelope(envelope: dict[str, Any]) -> None:
    schema = json.loads(
        files("app.engineering").joinpath("contracts/common.schema.json").read_text()
    )
    branch = next(
        (b for b in schema["oneOf"] if b["properties"]["kind"]["const"] == envelope.get("kind")),
        None,
    )
    if branch is None:
        raise ValueError("unknown_envelope_kind")
    _check(envelope, branch)
    kind, payload = envelope["kind"], envelope["payload"]
    patch = envelope["artifact_version"]["patch_sha"]
    if kind == "execution_result":
        if any(test["tested_sha"] != patch for test in payload["tests"]):
            raise ValueError("test_version_mismatch")
        if payload["executor_session_id"] != envelope["actor"]["session_id"]:
            raise ValueError("executor_session_mismatch")
        if payload["status"] == "succeeded":
            if patch is None or not payload["tests"] or payload["failure"] is not None:
                raise ValueError("success_requires_tested_patch")
            if any(t["exit_code"] != 0 or t["timed_out"] for t in payload["tests"]):
                raise ValueError("failed_test_blocks_success")
        elif payload["failure"] is None:
            raise ValueError("failure_requires_reason")
    if kind == "review":
        if patch is None or payload["reviewed_sha"] != patch:
            raise ValueError("review_version_mismatch")
        if payload["reviewer_session_id"] == payload["executor_session_id"]:
            raise ValueError("independent_reviewer_required")
        if payload["reviewer_session_id"] != envelope["actor"]["session_id"]:
            raise ValueError("reviewer_session_mismatch")
        if payload["status"] == "passed" and any(
            f["severity"] in {"blocker", "major"} and f["resolution"] == "open"
            for f in payload["findings"]
        ):
            raise ValueError("open_blocking_finding")


def _make(
    kind: Kind,
    *,
    run_id: str | UUID,
    task_id: str | UUID,
    base_sha: str,
    patch_sha: str | None,
    payload: dict[str, Any],
    principal_id: str,
    session_id: str,
    correlation_id: str,
    idempotency_key: str,
    evidence_refs: list[str],
    diff_sha256: str | None = None,
    manifest_sha256: str | None = None,
    synthetic: bool = False,
    event_id: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    refs = list(evidence_refs)
    if diff_sha256 is not None:
        if not re.fullmatch("[a-f0-9]{64}", diff_sha256):
            raise ValueError("invalid_diff_sha256")
        refs.append("sha256:diff:" + diff_sha256)
    result = {
        "contract_version": "cofounder-common-0.1",
        "kind": kind,
        "event_id": event_id or str(uuid4()),
        "run_id": str(run_id),
        "task_id": str(task_id),
        "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "actor": {
            "principal_id": principal_id,
            "kind": "service" if kind == "task" else "agent",
            "role": {"task": "controller", "execution_result": "executor", "review": "reviewer"}[
                kind
            ],
            "session_id": session_id,
        },
        "artifact_version": {
            "base_sha": base_sha,
            "patch_sha": patch_sha,
            "artifact_manifest_sha256": manifest_sha256,
        },
        "evidence_refs": refs,
        "synthetic": synthetic,
        "payload": copy.deepcopy(payload),
    }
    validate_envelope(result)
    return result


def task_envelope(*, payload: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    """Payload is the frozen task schema; max_attempts counts executions, not calls."""
    return _make("task", payload=payload, **metadata)


def execution_result_envelope(*, payload: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    """Tests must bind actual patch commit; failed or timed-out tests block success."""
    return _make("execution_result", payload=payload, **metadata)


def review_envelope(*, payload: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    """Caller translates findings blocking/warning/info to blocker/major/minor."""
    return _make("review", payload=payload, **metadata)


def engineering_envelopes(
    result: dict[str, Any],
    *,
    title: str,
    owner: str,
    allowed_paths: list[str],
    contract_sha256: str,
    evidence_dir: str,
    correlation_id: str,
) -> dict[str, dict[str, Any]]:
    """Adapt retained engineering evidence without inventing test measurements."""
    base, patch = result["base_sha"], result.get("candidate_commit")
    executor = result.get("executor", {}).get("session_id", result["workspace_id"])
    reviewer = result.get("reviewer", {}).get("session_id")
    metadata = dict(
        run_id=result["run_id"],
        task_id=result["task_id"],
        base_sha=base,
        patch_sha=patch,
        principal_id="engineering-agent",
        session_id=executor,
        correlation_id=correlation_id,
        idempotency_key=result["workspace_id"],
        evidence_refs=[evidence_dir + "/result.json"],
        diff_sha256=result.get("patch_sha"),
        synthetic=result["synthetic"],
    )
    cases = json.loads(
        files("app.engineering").joinpath("contracts/material-cases.json").read_text()
    )
    task = task_envelope(
        **metadata,
        payload={
            "title": title,
            "task_type": "insurance_material_completeness",
            "status": "waiting_approval"
            if result["state"] == "passed_checks_pending_delivery_approval"
            else "cancelled"
            if result["state"] == "cancelled"
            else "failed",
            "owner_principal_id": owner,
            "assigned_agent": "engineering-agent",
            "dependency_ids": [],
            "allowed_paths": allowed_paths,
            "constraints": {
                "privacy": "restricted",
                "allowed_providers": ["local"],
                "max_cost_usd": 0,
                "max_wall_seconds": 600,
                "max_attempts": 2,
                "max_repair_rounds": 2,
            },
            "acceptance_contract_sha256": contract_sha256,
            "required_case_ids": [c["case_id"] for c in cases["cases"]],
        },
    )
    state = result["state"]
    success = state == "passed_checks_pending_delivery_approval"
    status = (
        "succeeded"
        if success
        else {"cancelled": "cancelled", "timeout": "timed_out"}.get(state, "failed")
    )
    tests = [
        {
            "command": t["argv"],
            "cwd": t["cwd"],
            "exit_code": t["exit_code"],
            "timed_out": t["timed_out"],
            "tested_sha": patch,
            "duration_ms": t["duration_seconds"] * 1000,
            "log_ref": evidence_dir + f"/tests.json#/{index}",
        }
        for index, t in enumerate(result.get("tests", []))
    ]
    execution = execution_result_envelope(
        **metadata,
        payload={
            "status": status,
            "executor_session_id": executor,
            "claim_id": result["workspace_id"],
            "attempt": result["attempt"],
            "changed_paths": result.get("changed_files", []),
            "diff_ref": evidence_dir + "/patch.diff" if patch else None,
            "tests": tests,
            "failure": None
            if success
            else {
                "code": {"timeout": "timeout", "cancelled": "cancelled"}.get(
                    state, "execution_failed"
                ),
                "retryable": state not in {"cancelled"},
                "safe_detail": result.get("error", state),
            },
        },
    )
    envelopes = {"task": task, "execution_result": execution}
    review = result.get("review")
    if reviewer and review and patch:
        # A stale response cannot establish a passed review of the requested commit.
        conclusion = (
            review["conclusion"] if review["patch_sha"] == result["patch_sha"] else "inconclusive"
        )
        findings = [
            {
                "finding_id": f"finding-{index + 1}",
                "severity": {"blocking": "blocker", "warning": "minor", "info": "minor"}[
                    f["severity"]
                ],
                "path": f["path"],
                "line": f["line"],
                "trigger": f["trigger"],
                "impact": f["impact"],
                "evidence_ref": evidence_dir + f"/reviewer-response.json#finding-{index + 1}",
                "resolution": "open",
            }
            for index, f in enumerate(review["findings"])
        ]
        if any(f["severity"] in {"blocker", "major"} for f in findings) and conclusion == "passed":
            conclusion = "changes_requested"
        envelopes["review"] = review_envelope(
            **(metadata | {"session_id": reviewer, "principal_id": "independent-reviewer"}),
            payload={
                "status": conclusion,
                "reviewed_sha": patch,
                "reviewer_session_id": reviewer,
                "executor_session_id": executor,
                "context_ref": evidence_dir + "/reviewer-request.json",
                "findings": findings,
            },
        )
    return envelopes
