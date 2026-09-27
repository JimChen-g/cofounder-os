"""Workflow Controller extension: atomic version-bound delivery and repair intents."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.domain import AuditEvent, AuditOutcome, RunStatus, TaskStatus, utc_now
from app.engineering.workspace import ALLOWED


class DeliveryConflict(ValueError):
    pass


class VersionAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    base_sha: str = Field(pattern=r'^[a-f0-9]{40}$')
    patch_sha: str = Field(pattern=r'^[a-f0-9]{64}$')
    approval_id: str


class Feedback(VersionAction):
    artifact_id: UUID
    path: str
    line: int = Field(ge=1)
    comment: str = Field(min_length=1, max_length=2000)


class EngineeringDeliveryController:
    """All decisions live in one locked Run record; external payloads have no authority."""
    def __init__(self, product: Any) -> None:
        self.product = product
        self.repository = product.orchestration.repository

    @staticmethod
    def _event(tx: Any, run: Any, actor: str, action: str) -> None:
        tx.append_event(AuditEvent(run_id=run.id, actor=actor,
            event_type='engineering.' + action, action=action, outcome=AuditOutcome.SUCCESS,
            target_type='run', target_id=str(run.id), details={
                'revision': run.metadata.get('delivery', {}).get('revision')}))

    def prepare(self, run_id: Any) -> None:
        snapshot = self.product.get_run(run_id)
        artifacts = [a for a in snapshot.artifacts if a.name.startswith('engineering-result')]
        if not artifacts:
            raise DeliveryConflict('missing_candidate')
        artifact = max(artifacts, key=lambda a: a.created_at)
        result = self._read_artifact(artifact)
        self._checks(result)
        with self.repository.transaction(run_id) as tx:
            run = tx.get_run()
            if run.status != 'running':
                raise DeliveryConflict('run_not_running')
            if result['base_sha'] != run.metadata['base_sha']:
                raise DeliveryConflict('base_version_mismatch')
            old = run.metadata.get('delivery', {})
            if old.get('state') == 'cancelled':
                raise DeliveryConflict('cancelled')
            if old.get('patch_sha') == result['patch_sha']:
                run.metadata['termination_reason'] = 'no_progress'
                run.status = RunStatus.FAILED.value
                old['state'] = 'manual_required'
                tx.save_run(run)
                self._event(tx, run, 'workflow-controller', 'no_progress')
                return
            run.metadata['delivery'] = {
                'revision': old.get('revision', 0) + 1,
                'base_sha': result['base_sha'], 'patch_sha': result['patch_sha'],
                'candidate_commit': result['candidate_commit'],
                'artifact_id': str(artifact.id), 'artifact_sha256': artifact.checksum_sha256,
                'approval_id': str(uuid4()), 'state': 'pending',
                'expires_at': (utc_now() + timedelta(hours=1)).isoformat(),
                'repair_rounds': old.get('repair_rounds', 0),
                'feedback': old.get('feedback', []), 'receipts': old.get('receipts', {}),
                'history': old.get('history', [])}
            run.metadata['delivery_approved'] = False
            run.status = RunStatus.WAITING_APPROVAL.value
            run.updated_at = utc_now()
            tx.save_run(run)
            self._event(tx, run, 'workflow-controller', 'delivery_pending')

    @staticmethod
    def _checks(result: dict[str, Any]) -> None:
        patch = result['patch_sha']
        if ('review_schema' in result and result['review_schema'] not in
                ('engineering_review_v1', 'engineering_review_v2')):
            raise DeliveryConflict('checks_not_current')
        if result.get('review_schema') == 'engineering_review_v2':
            checks = result.get('review', {}).get('checks')
            required = {'input_shape', 'material_rules', 'filenames',
                        'output_contract', 'side_effects', 'tests'}
            if (not isinstance(checks, dict) or set(checks) != required
                    or any(not isinstance(check, dict)
                           or type(check.get('satisfied')) is not bool
                           or not check['satisfied'] for check in checks.values())):
                raise DeliveryConflict('checks_not_current')
        if (result.get('state') != 'passed_checks_pending_delivery_approval'
            or len(result.get('tests', [])) != 3
            or any(t.get('patch_sha') != patch or t.get('exit_code') != 0 or t.get('timed_out')
                   for t in result['tests'])
            or result.get('review', {}).get('patch_sha') != patch
            or result['review'].get('conclusion') != 'passed'
            or any(f['severity'] == 'blocking' for f in result['review'].get('findings', []))
            or not result.get('executor', {}).get('session_id')
            or not result.get('reviewer', {}).get('session_id')
            or result['executor']['session_id'] == result['reviewer']['session_id']):
            raise DeliveryConflict('checks_not_current')
        if hashlib.sha256(result['code_diff'].encode()).hexdigest() != patch:
            raise DeliveryConflict('patch_integrity')

    def _read_artifact(self, artifact: Any) -> dict[str, Any]:
        self.product.workflow_controller._verify_artifact(artifact)
        value: dict[str, Any] = self.product.artifact_store.read_json(artifact.run_id, artifact.metadata['logical_name'], artifact.metadata['filename'], artifact.task_id)
        return value

    def _result(self, tx: Any, delivery: dict[str, Any]) -> dict[str, Any]:
        artifact = tx.get_artifact(delivery['artifact_id'])
        if artifact.checksum_sha256 != delivery['artifact_sha256']:
            raise DeliveryConflict('artifact_integrity')
        result = self._read_artifact(artifact)
        self._checks(result)
        if any(result[key] != delivery[key] for key in ('base_sha', 'patch_sha', 'candidate_commit')):
            raise DeliveryConflict('version_integrity')
        return result

    def act(self, run_id: Any, actor: str, action: str, body: VersionAction) -> dict[str, Any]:
        from datetime import datetime
        with self.repository.transaction(run_id) as tx:
            run = tx.get_run()
            if run.owner != actor or not run.metadata.get('engineering'):
                raise DeliveryConflict('not_found')
            d = run.metadata.get('delivery', {})
            digest = hashlib.sha256(json.dumps({'action': action, **body.model_dump(mode='json')}, sort_keys=True).encode()).hexdigest()
            receipt = d.get('receipts', {}).get(body.request_id)
            if receipt:
                if receipt['digest'] != digest:
                    raise DeliveryConflict('idempotency_conflict')
                return dict(receipt['response']) | {'duplicate': True}
            if not (run.status == 'waiting_approval' and d.get('state') == 'pending') and not (action == 'cancel' and run.status == 'running' and d.get('state') == 'repair_queued'):
                raise DeliveryConflict('not_pending')
            if any(d.get(k) != getattr(body, k) for k in ('revision','base_sha','patch_sha','approval_id')):
                raise DeliveryConflict('stale_version')
            if datetime.fromisoformat(d['expires_at']) <= utc_now():
                raise DeliveryConflict('expired')
            result = self._result(tx, d)
            response: dict[str, Any] = {'action': action, 'run_id': str(run.id), 'revision': d['revision'], 'patch_sha': d['patch_sha'], 'duplicate': False}
            if action == 'approve':
                d['state'] = 'approved'
                d['decided_by'] = actor
                d['decided_at'] = utc_now().isoformat()
                run.metadata['delivery_approved'] = True
                run.status = RunStatus.COMPLETED.value
            elif action in {'reject','cancel'}:
                d['state'] = 'rejected' if action == 'reject' else 'cancelled'
                run.status = RunStatus.FAILED.value if action == 'reject' else RunStatus.CANCELLED.value
                run.metadata['termination_reason'] = d['state']
                for task in tx.list_tasks():
                    if task.status not in ('completed', 'failed', 'cancelled'):
                        task.status = TaskStatus.CANCELLED.value
                        tx.save_task(task)
            elif action == 'feedback' and isinstance(body, Feedback):
                if str(body.artifact_id) != d['artifact_id'] or body.path not in ALLOWED:
                    raise DeliveryConflict('feedback_location')
                # Locations are checked against the immutable diff, never arbitrary paths.
                from app.engineering.workspace import git
                content = git(self.product.workflow_controller.engineering_repo, 'show', result['candidate_commit'] + ':' + body.path)
                if body.line > len(content.splitlines()):
                    raise DeliveryConflict('feedback_location')
                tasks = [t for t in tx.list_tasks() if t.metadata.get('task_type') == 'engineering.materials']
                if len(tasks) != 1:
                    raise DeliveryConflict('task_scope')
                task = tasks[0]
                d['feedback'].append(body.model_dump(mode='json'))
                if d['repair_rounds'] >= 2 or task.attempt_count >= task.max_attempts:
                    d['state'] = 'manual_required'
                    run.status = RunStatus.FAILED.value
                    run.metadata['termination_reason'] = 'repair_or_attempt_limit'
                    response['action'] = 'manual_required'
                else:
                    d['history'].append({k: v for k, v in d.items() if k not in {'history','receipts','feedback'}})
                    d['repair_rounds'] += 1
                    d['state'] = 'repair_queued'
                    run.metadata['delivery_approved'] = False
                    run.status = RunStatus.RUNNING.value
                    task.status = TaskStatus.READY.value
                    task.metadata['repair_feedback'] = body.model_dump(mode='json')
                    task.metadata['previous_result'] = result
                    task.output_artifact_ids = []
                    task.claim_token = None
                    task.claimed_by = None
                    tx.save_task(task)
            else:
                raise DeliveryConflict('invalid_action')
            d['receipts'][body.request_id] = {'digest': digest, 'response': response}
            run.updated_at = utc_now()
            tx.save_run(run)
            self._event(tx, run, actor, action)
            return response

    def export(self, run_id: Any, actor: str) -> dict[str, Any]:
        with self.repository.transaction(run_id) as tx:
            run = tx.get_run()
            d = run.metadata.get('delivery', {})
            if run.owner != actor or run.status != 'completed' or d.get('state') != 'approved' or not run.metadata.get('delivery_approved'):
                raise DeliveryConflict('delivery_not_approved')
            result = self._result(tx, d)
            return {'run_id': str(run.id), 'approval': d, 'result': result}

    def interrupt(self, run_id: Any, actor: str, request_id: str,
                  base_sha: str, revision: int) -> dict[str, Any]:
        """Cancel a queued/in-flight version before a candidate exists, with CAS."""
        with self.repository.transaction(run_id) as tx:
            run = tx.get_run()
            if run.owner != actor or not run.metadata.get('engineering'):
                raise DeliveryConflict('not_found')
            receipts = run.metadata.setdefault('cancel_receipts', {})
            digest = hashlib.sha256(json.dumps([base_sha, revision]).encode()).hexdigest()
            if request_id in receipts:
                if receipts[request_id] != digest:
                    raise DeliveryConflict('idempotency_conflict')
                return {'cancelled': True, 'duplicate': True}
            if (run.status not in ('queued', 'running', 'waiting_approval')
                or run.metadata['base_sha'] != base_sha
                or run.metadata.get('delivery', {}).get('revision', 0) != revision):
                raise DeliveryConflict('stale_or_terminal')
            run.status = RunStatus.CANCELLED.value
            run.metadata['delivery_approved'] = False
            run.metadata['termination_reason'] = 'cancelled'
            if 'delivery' in run.metadata:
                run.metadata['delivery']['state'] = 'cancelled'
            receipts[request_id] = digest
            for task in tx.list_tasks():
                if task.status not in ('completed', 'failed', 'cancelled'):
                    task.status = TaskStatus.CANCELLED.value
                    tx.save_task(task)
            run.updated_at = utc_now()
            tx.save_run(run)
            self._event(tx, run, actor, 'cancelled')
            return {'cancelled': True, 'duplicate': False}
