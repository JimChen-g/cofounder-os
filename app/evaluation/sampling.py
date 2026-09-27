"""Small, sequential candidate sampling. Labels derive only from semantic checks.

This is a collection pipeline, not a trained router or held-out evaluation.
No model output is executed. The oracle is withheld from the candidate prompt.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.clients.gateway import GatewayClient, GatewayClientError
from app.provider_errors import ProviderError
from app.models import ChatMessage, Provider, Role
from app.policy.request_policy import InvocationBudget, active_budget
from app.request_constraints import RequestPolicy

Channel = Literal["local", "step"]
SCORER_VERSION = "missing-items-exact-v1"


@dataclass(frozen=True)
class SampleTask:
    task_id: str
    family: str
    source: str
    group_id: str
    prompt: str
    expected_missing: tuple[str, ...]
    policy: RequestPolicy
    run_id: str | None = None
    source_kind: str = "synthetic"
    dataset_version: str | None = None
    environment_digest: str | None = None
    agent_version: str | None = None
    price_ref: str | None = None

    def features(self) -> dict[str, Any]:
        """Only values available before any candidate invocation."""
        return {"task_kind": self.family, "input_size": len(self.prompt.encode()),
                "required_output_schema": SCORER_VERSION,
                "pre_call_context_budget": self.policy.max_total_tokens,
                "known_tool_requirements": [],
                "privacy": self.policy.privacy,
                "allowed_providers": sorted(self.policy.allowed_providers)}


def score_output(output: str, expected_missing: tuple[str, ...]) -> dict[str, Any]:
    """Strict JSON and exact missing-item semantics; no self-rating or approval."""
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        return {"structure_pass": False, "semantic_pass": False, "reason": "invalid_json"}
    structure = (isinstance(value, dict) and set(value) == {"missing", "complete"}
                 and isinstance(value["missing"], list)
                 and all(isinstance(item, str) for item in value["missing"])
                 and type(value["complete"]) is bool)
    if not structure:
        return {"structure_pass": False, "semantic_pass": False, "reason": "invalid_schema"}
    semantic = (len(value["missing"]) == len(set(value["missing"]))
                and set(value["missing"]) == set(expected_missing)
                and value["complete"] == (len(expected_missing) == 0))
    return {"structure_pass": True, "semantic_pass": semantic,
            "reason": "exact_match" if semantic else "wrong_missing_items_or_complete"}


def derive_label(records: list[dict[str, Any]]) -> str:
    """Rebuild a label exclusively from recorded external checks."""
    passing = {r["candidate"] for r in records
               if r.get("error") is None and r["checks"]["semantic_pass"]
               and r["checks"]["structure_pass"]}
    return "both" if passing == {"local", "step"} else next(iter(passing), "neither")


async def sample_task(task: SampleTask, client: GatewayClient, output_dir: Path,
                      *, max_tokens: int = 1024) -> dict[str, Any]:
    """Invoke each permitted channel once, serially, retaining raw outputs.

    The caller grants the overall policy; this method narrows it per call. Cost is
    deliberately unknown: token counts are not converted into Step Plan Credits.
    Output directories must be private when prompts contain private data.
    """
    if not task.task_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in task.task_id):
        raise ValueError("task_id must be a safe filename")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{task.task_id}.json"
    if target.exists():
        raise FileExistsError(target)
    records: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema_version": "t15-sampling-1", "task_id": task.task_id,
        "task_family": task.family, "source": task.source, "group_id": task.group_id,
        "split": "collection_only", "features_before_call": task.features(),
        "prompt": task.prompt, "prompt_sha256": hashlib.sha256(task.prompt.encode()).hexdigest(),
        "oracle": {"expected_missing": list(task.expected_missing)},
        "scorer_version": SCORER_VERSION, "policy": task.policy.model_dump(mode="json"),
        "created_at": datetime.now(timezone.utc).isoformat(), "candidates": records,
        "label": "pending", "cost": {"amount": None, "unit": "unknown",
        "basis": "provider usage recorded; Step Plan Credits not inferred"},
    }
    # Exclusive creation prevents silently overwriting earlier evidence.
    with target.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    budget = InvocationBudget(task.policy)
    token = active_budget.set(budget)
    try:
        for channel, provider in (("local", Provider.QWEN), ("step", Provider.STEP)):
            if not task.policy.allows(provider):
                continue
            policy = task.policy.model_copy(update={"allowed_providers": frozenset({channel}),
                                                    "max_attempts": task.policy.max_attempts})
            record: dict[str, Any] = {"candidate": channel, "output": None, "error": None,
                                     "usage": {}, "checks": {"structure_pass": False,
                                     "semantic_pass": False, "reason": "not_run"}}
            record["started_at"] = datetime.now(timezone.utc).isoformat()
            started = time.monotonic()
            try:
                response = await client.complete(
                    [ChatMessage(role=Role.SYSTEM, content='Return only JSON with keys "missing" '
                     '(array of missing item names) and "complete" (boolean).'),
                     ChatMessage(role=Role.USER, content=task.prompt)],
                    model="cofounder-auto", policy=policy, max_tokens=max_tokens, temperature=0)
                record.update(output=response.content, usage=response.usage,
                              selected_provider=response.selected_provider,
                              selected_model=response.selected_model, request_id=response.request_id,
                              fallback_used=response.fallback_used)
                # A misrouted candidate must never acquire the requested channel's label.
                expected_provider = {"local": {"local", "qwen"}, "step": {"step"}}[channel]
                if response.selected_provider not in expected_provider or response.fallback_used:
                    record["error"] = "provider_identity_mismatch"
                else:
                    record["checks"] = score_output(response.content, task.expected_missing)
            except Exception as exc:
                # Do not persist transport exception text: it can contain endpoint secrets.
                record["error"] = type(exc).__name__
                if isinstance(exc, (GatewayClientError, ProviderError)) and str(exc) in {
                        "request_budget_exhausted", "provider_not_allowed", "no_legal_provider"}:
                    record["error"] = str(exc)
                cause = exc.__cause__
                if cause is not None and "Timeout" in type(cause).__name__:
                    record["error"] = "timeout"
            record["ended_at"] = datetime.now(timezone.utc).isoformat()
            record["elapsed_seconds"] = round(time.monotonic() - started, 6)
            records.append(record)
            observation = contract_observation(task, record, str(target))
            (output_dir / f"{task.task_id}-{channel}.record.json").write_text(
                json.dumps(observation, ensure_ascii=False, indent=2))
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        active_budget.reset(token)
    result["label"] = derive_label(records)
    result["attempts_reserved"] = budget.attempts
    result["tokens_reserved"] = budget.reserved_tokens
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def contract_observation(task: SampleTask, record: dict[str, Any], evidence_path: str) -> dict[str, Any]:
    """T05 1.0.0 candidate observation; absent measurements remain null.

    All template/size/missing/order variants must share task.group_id. These first
    observations remain smoke data and must not silently enter a held-out split.
    """
    passed = record["error"] is None and record["checks"]["semantic_pass"]
    usage = record["usage"]
    versions = dict.fromkeys(("dataset", "scorer", "skill", "model", "engine", "agent",
                             "tools", "environment", "input_sha256", "base_sha", "patch_sha"))
    versions.update(dataset=task.dataset_version, scorer=SCORER_VERSION,
                    model=record.get("selected_model"), agent=task.agent_version,
                    environment=task.environment_digest,
                    input_sha256=hashlib.sha256(task.prompt.encode()).hexdigest())
    status = {"timeout": "timeout", "request_budget_exhausted": "budget_exhausted",
              "provider_not_allowed": "denied", "no_legal_provider": "denied"}.get(
                  record["error"], "succeeded" if passed else "failed")
    return {
        "schema_version": "1.0.0", "record_kind": "actual_observation",
        "experiment_id": "E02-routing-learning", "run_id": task.run_id or task.task_id,
        "task_id": task.task_id, "case_id": task.task_id, "family_id": task.group_id,
        "split": "smoke", "source_kind": task.source_kind, "source_ref": task.source,
        "pair_id": f"{task.task_id}-attempt-1", "arm": record["candidate"], "attempt": 1,
        "versions": versions, "pre_call_features": task.features(),
        "runtime": {"status": status,
                    "provider": record.get("selected_provider"),
                    "request_id": record.get("request_id"), "cache_state": "unknown",
                    "concurrency": 1, "started_at": record["started_at"],
                    "ended_at": record["ended_at"], "timeout_seconds": task.policy.timeout_seconds,
                    "retry_count": 0, "latency_ms": record["elapsed_seconds"] * 1000,
                    "peak_memory_bytes": None},
        "measurement": {"passed": passed, "score": int(passed), "score_kind": "deterministic",
                        "decision": None, "abstain_reason": record["error"],
                        "policy_violation": record["error"] == "provider_identity_mismatch",
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"), "tool_calls": 0,
                        "cost": None, "currency": None, "price_ref": task.price_ref,
                        "billing_status": "unknown"},
        "evidence": {"request": evidence_path + "#/prompt",
                     "response": evidence_path + " (candidate=" + record["candidate"] + ")",
                     "test_command": "app.evaluation.sampling.score_output(output, expected_missing)",
                     "test_exit_code": 0 if passed else 1,
                     "test_log": json.dumps(record["checks"], sort_keys=True), "review": None,
                     "label_basis": SCORER_VERSION + ": strict structure + exact oracle + provider policy"},
    }
