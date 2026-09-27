import copy

import pytest

from app.engineering.envelopes import (
    task_envelope,
    execution_result_envelope,
    review_envelope,
    validate_envelope,
)

EXAMPLES = {
    "execution_result": {
        "actor": {
            "kind": "agent",
            "principal_id": "example:executor",
            "role": "executor",
            "session_id": "executor-session-001",
        },
        "artifact_version": {
            "artifact_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "base_sha": "40509e83994fdb4366aec07a216c0f5b40310378",
            "patch_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
        "contract_version": "cofounder-common-0.1",
        "correlation_id": "synthetic-contract-example-001",
        "event_id": "00000000-0000-4000-8000-000000000002",
        "evidence_refs": ["fixture-only:not-runtime-evidence"],
        "idempotency_key": "execution_result:synthetic-001",
        "kind": "execution_result",
        "occurred_at": "2026-09-26T13:30:00Z",
        "payload": {
            "attempt": 1,
            "changed_paths": [
                "app/insurance_poc/materials.py",
                "tests/test_insurance_poc_materials.py",
            ],
            "claim_id": "opaque-reference-not-secret-token",
            "diff_ref": "fixture-only:patch.diff",
            "executor_session_id": "executor-session-001",
            "failure": None,
            "status": "succeeded",
            "tests": [
                {
                    "command": [
                        "python",
                        "-m",
                        "pytest",
                        "-q",
                        "tests/test_insurance_poc_materials.py",
                    ],
                    "cwd": "isolated-worktree",
                    "duration_ms": 1,
                    "exit_code": 0,
                    "log_ref": "fixture-only:test.log",
                    "tested_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "timed_out": False,
                }
            ],
        },
        "run_id": "11111111-1111-4111-8111-111111111111",
        "synthetic": True,
        "task_id": "22222222-2222-4222-8222-222222222222",
    },
    "review": {
        "actor": {
            "kind": "agent",
            "principal_id": "example:reviewer",
            "role": "reviewer",
            "session_id": "reviewer-session-001",
        },
        "artifact_version": {
            "artifact_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "base_sha": "40509e83994fdb4366aec07a216c0f5b40310378",
            "patch_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
        "contract_version": "cofounder-common-0.1",
        "correlation_id": "synthetic-contract-example-001",
        "event_id": "00000000-0000-4000-8000-000000000004",
        "evidence_refs": ["fixture-only:not-runtime-evidence"],
        "idempotency_key": "review:synthetic-001",
        "kind": "review",
        "occurred_at": "2026-09-26T13:30:00Z",
        "payload": {
            "context_ref": "fixture-only:independent-review-context",
            "executor_session_id": "executor-session-001",
            "findings": [],
            "reviewed_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "reviewer_session_id": "reviewer-session-001",
            "status": "passed",
        },
        "run_id": "11111111-1111-4111-8111-111111111111",
        "synthetic": True,
        "task_id": "22222222-2222-4222-8222-222222222222",
    },
    "task": {
        "actor": {
            "kind": "human",
            "principal_id": "example:founder",
            "role": "founder",
            "session_id": "founder-session-001",
        },
        "artifact_version": {
            "artifact_manifest_sha256": None,
            "base_sha": "40509e83994fdb4366aec07a216c0f5b40310378",
            "patch_sha": None,
        },
        "contract_version": "cofounder-common-0.1",
        "correlation_id": "synthetic-contract-example-001",
        "event_id": "00000000-0000-4000-8000-000000000001",
        "evidence_refs": ["fixture-only:not-runtime-evidence"],
        "idempotency_key": "task:synthetic-001",
        "kind": "task",
        "occurred_at": "2026-09-26T13:30:00Z",
        "payload": {
            "acceptance_contract_sha256": "7726766dc30bbb25f4c645cddf0321699b1e8ebcddea1c62cac1abb09337e05c",
            "allowed_paths": [
                "app/insurance_poc/materials.py",
                "tests/test_insurance_poc_materials.py",
            ],
            "assigned_agent": "engineering-agent",
            "constraints": {
                "allowed_providers": ["local"],
                "max_attempts": 2,
                "max_cost_usd": 0,
                "max_repair_rounds": 2,
                "max_wall_seconds": 600,
                "privacy": "restricted",
            },
            "dependency_ids": [],
            "owner_principal_id": "example:founder",
            "required_case_ids": [
                "complete",
                "missing_damage",
                "empty_list",
                "permuted_input",
                "only_damage",
                "duplicate_id",
                "duplicate_filename",
                "wrong_content_type_for_slot",
                "path_traversal",
                "blank_filename",
                "missing_materials",
                "unknown_field",
                "wrong_collection_type",
                "unknown_slot",
            ],
            "status": "pending",
            "task_type": "insurance_material_completeness",
            "title": "保险 POC 材料清单完整性检查",
        },
        "run_id": "11111111-1111-4111-8111-111111111111",
        "synthetic": True,
        "task_id": "22222222-2222-4222-8222-222222222222",
    },
}


@pytest.mark.parametrize(
    "kind,factory",
    [
        ("task", task_envelope),
        ("execution_result", execution_result_envelope),
        ("review", review_envelope),
    ],
)
def test_frozen_examples_and_factories(kind, factory):
    original = copy.deepcopy(EXAMPLES[kind])
    validate_envelope(original)
    envelope = factory(
        payload=original["payload"],
        run_id=original["run_id"],
        task_id=original["task_id"],
        base_sha=original["artifact_version"]["base_sha"],
        patch_sha=original["artifact_version"]["patch_sha"],
        principal_id="test-agent",
        session_id=original["actor"]["session_id"],
        correlation_id="test",
        idempotency_key="test-" + kind,
        evidence_refs=["fixture:only"],
        diff_sha256="f" * 64,
        synthetic=True,
    )
    assert envelope["evidence_refs"][-1] == "sha256:diff:" + "f" * 64
    assert envelope["artifact_version"]["patch_sha"] != "f" * 64
    envelope["payload"]["status"] = "mutated"
    assert original["payload"]["status"] != "mutated"


@pytest.mark.parametrize(
    "mutation",
    [
        "digest_as_commit",
        "extra",
        "failed_test",
        "wrong_version",
        "no_tests",
        "excess_attempt",
        "bad_uuid",
    ],
)
def test_execution_rejects_invalid_evidence(mutation):
    e = copy.deepcopy(EXAMPLES["execution_result"])
    if mutation == "digest_as_commit":
        e["artifact_version"]["patch_sha"] = "a" * 64
    if mutation == "extra":
        e["payload"]["delivery_approved"] = True
    if mutation == "failed_test":
        e["payload"]["tests"][0]["exit_code"] = 1
    if mutation == "wrong_version":
        e["payload"]["tests"][0]["tested_sha"] = "b" * 40
    if mutation == "no_tests":
        e["payload"]["tests"] = []
    if mutation == "excess_attempt":
        e["payload"]["attempt"] = 3
    if mutation == "bad_uuid":
        e["run_id"] = "not-uuid"
    with pytest.raises(ValueError):
        validate_envelope(e)


@pytest.mark.parametrize("mutation", ["self_review", "wrong_version", "blocker"])
def test_review_gates(mutation):
    e = copy.deepcopy(EXAMPLES["review"])
    if mutation == "self_review":
        e["payload"]["executor_session_id"] = e["payload"]["reviewer_session_id"]
    if mutation == "wrong_version":
        e["payload"]["reviewed_sha"] = "b" * 40
    if mutation == "blocker":
        e["payload"]["findings"] = [
            {
                "finding_id": "f1",
                "severity": "blocker",
                "path": "x.py",
                "line": 1,
                "trigger": "empty input",
                "impact": "crash",
                "evidence_ref": "fixture:log",
                "resolution": "open",
            }
        ]
    with pytest.raises(ValueError):
        validate_envelope(e)
