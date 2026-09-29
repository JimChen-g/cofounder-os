"""Synthetic governance fixtures, not live model or user approval evidence."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from tests.engineering_review_helpers import synthetic_review_checks

from app.clients.gateway import GatewayClient, GatewayCompletion
from app.config import Settings
from app.domain import utc_now
from app.engineering.service import EngineeringService
from app.engineering.workspace import ALLOWED, Workspace
from app.services.engineering_delivery import DeliveryConflict, Feedback, VersionAction
from app.services.product_api import build_product_api_service
from tests.test_engineering_execution import repo as engineering_repo_fixture

repo = engineering_repo_fixture


class SyntheticGateway(GatewayClient):
    def __init__(self):
        super().__init__('http://invalid')
        self.implementations = 0
        self.reviews = 0
        self.same_patch = False

    async def complete(self, messages, **kwargs):
        if 'implementation Agent' in messages[0].content:
            self.implementations += 1
            revision = 1 if self.same_patch else self.implementations
            content = ({'old': '# synthetic revision 1', 'new': f'# synthetic revision {revision}'} if 'bounded repair' in messages[0].content else {'implementation': f'# synthetic revision {revision}\nsynthetic_fixture = True\n', 'tests': '# fixture\nsynthetic_fixture = True\n'})
        else:
            self.reviews += 1
            content = {'patch_sha': json.loads(messages[1].content)['patch_sha'],
                       'checks': synthetic_review_checks(messages), 'conclusion': 'passed', 'findings': []}
        return GatewayCompletion(content=json.dumps(content), requested_model='synthetic')


@pytest.fixture
def env(repo, tmp_path, monkeypatch):
    product = build_product_api_service(Settings(PRODUCT_DATA_DIR=str(tmp_path / 'data')))
    service = EngineeringService(product, repo, tmp_path / 'tasks')
    service.gateway = SyntheticGateway()
    monkeypatch.setattr(Workspace, 'test', lambda self, argv, image: {
        'exit_code': 0, 'timed_out': False, 'patch_sha': self.verify(),
        'log': 'synthetic test fixture', 'argv': argv, 'cwd': 'synthetic-sandbox', 'duration_seconds': 0.001})
    return product, service, product.workflow_controller.engineering_delivery


def action(product, run_id, **overrides):
    d = product.get_run(run_id).run.metadata['delivery']
    return VersionAction(**({k: d[k] for k in ('revision', 'base_sha', 'patch_sha', 'approval_id')}
                            | {'request_id': str(uuid4())} | overrides))


def feedback(product, run_id, **overrides):
    d = product.get_run(run_id).run.metadata['delivery']
    return Feedback(**(action(product, run_id).model_dump() | {
        'artifact_id': d['artifact_id'], 'path': ALLOWED[0], 'line': 1,
        'comment': 'Synthetic fixture: revise the implementation.'} | overrides))


async def ready(env):
    product, service, controller = env
    run_id = service.create('founder', str(uuid4())).run.id
    assert (await service.execute(run_id)).status == 'waiting_approval'
    return run_id


@pytest.mark.asyncio
async def test_approval_export_and_replay_bound_to_current_candidate(env):
    p, s, c = env
    rid = await ready(env)
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'founder')
    body = action(p, rid)
    with pytest.raises(DeliveryConflict, match='not_found'):
        c.act(rid, 'stranger', 'approve', body)
    first = c.act(rid, 'founder', 'approve', body)
    assert not first['duplicate']
    assert c.act(rid, 'founder', 'approve', body)['duplicate']
    with pytest.raises(DeliveryConflict, match='idempotency_conflict'):
        c.act(rid, 'founder', 'reject', body)
    exported = c.export(rid, 'founder')
    assert exported['result']['patch_sha'] == body.patch_sha
    assert exported['approval']['decided_by'] == 'founder'
    assert exported['result']['base_sha'] == body.base_sha
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'stranger')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['reject', 'cancel'])
async def test_terminal_decision_cannot_advance(env, kind):
    p, s, c = env
    rid = await ready(env)
    c.act(rid, 'founder', kind, action(p, rid))
    with pytest.raises(DeliveryConflict, match='not_pending'):
        c.act(rid, 'founder', 'approve', action(p, rid))
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'founder')


@pytest.mark.asyncio
async def test_expired_and_stale_decisions_rejected(env):
    p, s, c = env
    rid = await ready(env)
    with pytest.raises(DeliveryConflict, match='stale_version'):
        c.act(rid, 'founder', 'approve', action(p, rid, patch_sha='0' * 64))
    with p.orchestration.repository.transaction(rid) as tx:
        run = tx.get_run()
        run.metadata['delivery']['expires_at'] = (utc_now() - timedelta(seconds=1)).isoformat()
        tx.save_run(run)
    with pytest.raises(DeliveryConflict, match='expired'):
        c.act(rid, 'founder', 'approve', action(p, rid))


@pytest.mark.asyncio
async def test_feedback_same_run_fresh_checks_and_two_round_cap(env):
    p, s, c = env
    rid = await ready(env)
    previous = action(p, rid)
    initial = p.get_run(rid).run.metadata['delivery'].copy()
    for revision in (2,):
        body = feedback(p, rid)
        c.act(rid, 'founder', 'feedback', body)
        assert c.act(rid, 'founder', 'feedback', body)['duplicate']
        assert (await s.execute(rid)).status == 'waiting_approval'
        d = p.get_run(rid).run.metadata['delivery']
        assert d['revision'] == revision
        assert d['artifact_id'] != initial['artifact_id']
        assert d['base_sha'] == initial['base_sha']
        assert d['patch_sha'] != initial['patch_sha']
        assert not p.get_run(rid).run.metadata['delivery_approved']
        with pytest.raises(DeliveryConflict, match='stale_version'):
            c.act(rid, 'founder', 'approve', previous)
    assert s.gateway.implementations == s.gateway.reviews == 2
    before = p.get_run(rid).model_dump(mode='json')
    with pytest.raises(DeliveryConflict, match='repair_attempts_exhausted'):
        c.act(rid, 'founder', 'feedback', feedback(p, rid))
    assert p.get_run(rid).model_dump(mode='json') == before


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [{'path': '../secret'}, {'line': 999}, {'artifact_id': str(uuid4())}])
async def test_feedback_requires_original_artifact_location(env, bad):
    p, s, c = env
    rid = await ready(env)
    with pytest.raises(DeliveryConflict, match='feedback_location'):
        c.act(rid, 'founder', 'feedback', feedback(p, rid, **bad))
    assert p.get_run(rid).run.metadata['delivery']['repair_rounds'] == 0


@pytest.mark.asyncio
async def test_no_progress_stops_without_approval(env):
    p, s, c = env
    s.gateway.same_patch = True
    rid = await ready(env)
    c.act(rid, 'founder', 'feedback', feedback(p, rid))
    assert (await s.execute(rid)).status == 'failed'
    assert p.get_run(rid).run.metadata['termination_reason'] == 'feedback_no_source_change'
    assert s.gateway.reviews == 1
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'founder')


@pytest.mark.asyncio
async def test_cancel_queued_repair_prevents_further_execution(env):
    p, s, c = env
    rid = await ready(env)
    c.act(rid, 'founder', 'feedback', feedback(p, rid))
    c.act(rid, 'founder', 'cancel', action(p, rid))
    assert (await s.execute(rid)).status == 'cancelled'
    assert s.gateway.implementations == 1


@pytest.mark.asyncio
async def test_restart_marks_durable_repair_interrupted(env, tmp_path, monkeypatch):
    """Process-restart injection; no real process or user approval involved."""
    from types import SimpleNamespace
    import app.api.engineering as api
    p, s, c = env
    rid = await ready(env)
    c.act(rid, 'founder', 'feedback', feedback(p, rid))
    monkeypatch.setattr(api, '_service', lambda request: p)
    monkeypatch.setattr(api, 'get_settings', lambda: SimpleNamespace(product_data_dir=str(tmp_path / 'restart')))
    monkeypatch.setenv('ENGINEERING_REPO', str(s.repo))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    runtime = api._runtime(request)
    assert p.get_run(rid).run.status == 'failed'
    assert p.get_run(rid).run.metadata['termination_reason'] == 'repair_interrupted_restart'
    assert s.gateway.implementations == 1
    runtime[0].lock.close()


@pytest.mark.asyncio
async def test_artifact_tamper_blocks_approval_and_export(env, monkeypatch):
    p, s, c = env
    rid = await ready(env)
    artifact_id = p.get_run(rid).run.metadata['delivery']['artifact_id']
    with p.orchestration.repository.transaction(rid) as tx:
        artifact = tx.get_artifact(artifact_id)
    result = c._read_artifact(artifact)
    result['code_diff'] += '# injected tamper'
    with pytest.raises(DeliveryConflict, match='patch_integrity'):
        c._checks(result)
    result = c._read_artifact(artifact)
    result['tests'][0]['patch_sha'] = '0' * 64
    with pytest.raises(DeliveryConflict, match='checks_not_current'):
        c._checks(result)
    result = c._read_artifact(artifact)
    result['review']['patch_sha'] = '0' * 64
    with pytest.raises(DeliveryConflict, match='checks_not_current'):
        c._checks(result)
    result = c._read_artifact(artifact)
    result['reviewer']['session_id'] = result['executor']['session_id']
    with pytest.raises(DeliveryConflict, match='checks_not_current'):
        c._checks(result)
    result = c._read_artifact(artifact)
    c.act(rid, 'founder', 'approve', action(p, rid))
    result['code_diff'] += '# tampered after approval'
    monkeypatch.setattr(c, '_read_artifact', lambda artifact: result)
    with pytest.raises(DeliveryConflict, match='patch_integrity'):
        c.export(rid, 'founder')


@pytest.mark.asyncio
async def test_active_repair_cancel_preserves_cancelled_state(env):
    """Synthetic blocked model call exercises actual API task cancellation."""
    import asyncio
    from types import SimpleNamespace
    from app.api.engineering import _act
    p, s, c = env
    rid = await ready(env)
    entered = asyncio.Event()
    async def blocked_completion(messages, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    s.gateway.complete = blocked_completion
    tasks = set()
    state = SimpleNamespace(engineering_runtime=(None, s, p, tasks, asyncio.Lock()))
    request = SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(principal='founder'))
    await _act(request, rid, 'feedback', feedback(p, rid))
    await asyncio.wait_for(entered.wait(), timeout=5)
    running = list(tasks)
    await _act(request, rid, 'cancel', action(p, rid))
    await asyncio.gather(*running, return_exceptions=True)
    assert p.get_run(rid).run.status == 'cancelled'
    assert p.get_run(rid).run.metadata['termination_reason'] == 'cancelled'
    assert not p.get_run(rid).run.metadata['delivery_approved']
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'founder')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,expected_reason', [
    ('model_timeout', 'timeout'), ('budget_exhausted', 'budget_or_policy_denied'),
    ('test_timeout', 'timeout'),
])
async def test_feedback_failure_has_visible_terminal_reason(env, monkeypatch, failure, expected_reason):
    """Fault injection after a passed initial synthetic version, before any approval."""
    import httpx
    p, s, c = env
    rid = await ready(env)
    old = p.get_run(rid).run.metadata['delivery'].copy()
    c.act(rid, 'founder', 'feedback', feedback(p, rid))
    if failure == 'model_timeout':
        def timed_out(request):
            raise httpx.ReadTimeout('synthetic upstream timeout', request=request)
        s.gateway = GatewayClient('http://synthetic.invalid', transport=httpx.MockTransport(timed_out))
    elif failure == 'budget_exhausted':
        key = p.get_run(rid).run.metadata['engineering_budget_id']
        with s.budgets.connect() as db:
            db.execute('UPDATE budgets SET attempts=999 WHERE id=?', (key,))
        def forbidden_transport(request):
            pytest.fail('exhausted budget must stop before transport')
        s.gateway = GatewayClient('http://synthetic.invalid', transport=httpx.MockTransport(forbidden_transport))
    else:
        monkeypatch.setattr(Workspace, 'test', lambda self, argv, image: {
            'exit_code': 124, 'timed_out': True, 'patch_sha': self.verify(),
            'log': 'synthetic sandbox timeout', 'argv': argv,
            'cwd': 'synthetic-sandbox', 'duration_seconds': 0.001})
    assert (await s.execute(rid)).status == 'failed'
    run = p.get_run(rid).run
    assert run.metadata['termination_reason'] == expected_reason
    assert run.metadata['delivery']['revision'] == old['revision']
    assert not run.metadata['delivery_approved']
    assert all(t.attempt_count <= 2 for t in p.get_run(rid).tasks)
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'founder')


@pytest.mark.asyncio
async def test_restart_receipt_replay_does_not_requeue_or_approve(env, tmp_path):
    from app.services.engineering_delivery import EngineeringDeliveryController
    p, s, c = env
    rid = await ready(env)
    body = feedback(p, rid)
    c.act(rid, 'founder', 'feedback', body)
    p.orchestration.fail_run(rid, actor='workflow-controller', reason='synthetic_restart')
    restarted = build_product_api_service(Settings(PRODUCT_DATA_DIR=str(tmp_path / 'data')))
    restored_controller = EngineeringDeliveryController(restarted)
    result = restored_controller.act(rid, 'founder', 'feedback', body)
    assert result['duplicate']
    assert restarted.get_run(rid).run.status == 'failed'
    assert restarted.get_run(rid).run.metadata['delivery']['repair_rounds'] == 1
    with pytest.raises(DeliveryConflict):
        restored_controller.act(rid, 'founder', 'approve', action(restarted, rid))


@pytest.mark.asyncio
async def test_late_model_result_after_cancel_never_publishes_candidate(env):
    """Cancellation races a model result; the model returns after cancellation commits."""
    import asyncio
    p, s, c = env
    rid = await ready(env)
    old_artifacts = {a.id for a in p.get_run(rid).artifacts if a.name.startswith('engineering-result')}
    entered, release = asyncio.Event(), asyncio.Event()
    original = s.gateway.complete
    async def late_completion(messages, **kwargs):
        if 'implementation Agent' in messages[0].content:
            entered.set()
            await release.wait()
        return await original(messages, **kwargs)
    s.gateway.complete = late_completion
    c.act(rid, 'founder', 'feedback', feedback(p, rid))
    execution = asyncio.create_task(s.execute(rid))
    await asyncio.wait_for(entered.wait(), timeout=5)
    c.act(rid, 'founder', 'cancel', action(p, rid))
    release.set()
    await asyncio.gather(execution, return_exceptions=True)
    current = p.get_run(rid)
    assert current.run.status == 'cancelled'
    assert not current.run.metadata['delivery_approved']
    assert {a.id for a in current.artifacts if a.name.startswith('engineering-result')} == old_artifacts
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'founder')


def test_initial_interrupt_owner_cas_and_idempotency(env):
    p, s, c = env
    snapshot = s.create('founder', str(uuid4()))
    rid, base = snapshot.run.id, snapshot.run.metadata['base_sha']
    request_id = str(uuid4())
    with pytest.raises(DeliveryConflict, match='not_found'):
        c.interrupt(rid, 'stranger', request_id, base, 0)
    with pytest.raises(DeliveryConflict, match='stale_or_terminal'):
        c.interrupt(rid, 'founder', request_id, '0' * 40, 0)
    with pytest.raises(DeliveryConflict, match='stale_or_terminal'):
        c.interrupt(rid, 'founder', request_id, base, 1)
    assert not c.interrupt(rid, 'founder', request_id, base, 0)['duplicate']
    assert c.interrupt(rid, 'founder', request_id, base, 0)['duplicate']
    with pytest.raises(DeliveryConflict, match='idempotency_conflict'):
        c.interrupt(rid, 'founder', request_id, base, 1)
    with pytest.raises(DeliveryConflict, match='stale_or_terminal'):
        c.interrupt(rid, 'founder', str(uuid4()), base, 0)
    assert p.get_run(rid).run.status == 'cancelled'
    assert all(t.status == 'cancelled' for t in p.get_run(rid).tasks)


@pytest.mark.asyncio
async def test_initial_running_interrupt_cancels_active_api_task(env):
    import asyncio
    from types import SimpleNamespace
    from app.api.engineering import InterruptRun, interrupt
    p, s, c = env
    snapshot = s.create('founder', str(uuid4()))
    rid = snapshot.run.id
    entered = asyncio.Event()
    async def blocked_completion(messages, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    s.gateway.complete = blocked_completion
    execution = asyncio.create_task(s.execute(rid))
    state = SimpleNamespace(engineering_runtime=(None, s, p, {execution}, asyncio.Lock()),
                            engineering_active={str(rid): execution})
    request = SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(principal='founder'))
    await asyncio.wait_for(entered.wait(), timeout=5)
    response = await interrupt(request, rid, InterruptRun(request_id=str(uuid4()),
        base_sha=snapshot.run.metadata['base_sha'], revision=0))
    assert response['cancelled']
    await asyncio.gather(execution, return_exceptions=True)
    current = p.get_run(rid)
    assert current.run.status == 'cancelled'
    assert current.run.metadata['termination_reason'] == 'cancelled'
    assert not any(a.name.startswith('engineering-result') for a in current.artifacts)


@pytest.mark.asyncio
@pytest.mark.parametrize('edit', [
    {'old': 'absent anchor', 'new': 'replacement'},
    {'old': 'i', 'new': 'replacement'},
    {'old': '', 'new': 'replacement'},
    {'old': '# synthetic revision 1', 'new': 'replacement', 'path': '../secret'},
])
async def test_repair_invalid_anchor_or_extra_scope_fails_closed(env, edit):
    p, s, c = env
    rid = await ready(env)
    original = p.get_run(rid).run.metadata['delivery'].copy()
    async def invalid_edit(messages, **kwargs):
        return GatewayCompletion(content=json.dumps(edit), requested_model='synthetic')
    s.gateway.complete = invalid_edit
    c.act(rid, 'founder', 'feedback', feedback(p, rid))
    assert (await s.execute(rid)).status == 'failed'
    current = p.get_run(rid)
    assert current.run.metadata['delivery']['patch_sha'] == original['patch_sha']
    assert current.run.metadata['delivery']['revision'] == original['revision']
    assert sum(a.name.startswith('engineering-result') for a in current.artifacts) == 1
    assert s.gateway.reviews == 1
    assert all(t.attempt_count <= 2 for t in current.tasks)
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'founder')


@pytest.mark.asyncio
@pytest.mark.parametrize('target_index', [0, 1])
async def test_bounded_repair_preserves_untargeted_file_and_constraints(env, target_index):
    from app.engineering.workspace import git
    p, s, c = env
    rid = await ready(env)
    before = p.get_run(rid).run.metadata['delivery'].copy()
    initial_files = {path: git(s.repo, 'show', before['candidate_commit'] + ':' + path) for path in ALLOWED}
    target = ALLOWED[target_index]
    original_complete = s.gateway.complete
    async def minimal_edit(messages, **kwargs):
        policy = kwargs['policy']
        assert policy.max_attempts == 4 and policy.max_total_tokens == 100000
        assert policy.timeout_seconds == 600
        if 'bounded repair' in messages[0].content:
            assert kwargs['max_tokens'] == 1200
            return GatewayCompletion(content=json.dumps({'old': initial_files[target],
                'new': initial_files[target] + '# bounded synthetic change\n'}), requested_model='synthetic')
        assert kwargs['max_tokens'] == 3000
        return await original_complete(messages, **kwargs)
    s.gateway.complete = minimal_edit
    c.act(rid, 'founder', 'feedback', feedback(p, rid, path=target))
    assert (await s.execute(rid)).status == 'waiting_approval'
    current = p.get_run(rid).run
    after = current.metadata['delivery']
    assert after['base_sha'] == before['base_sha']
    assert after['patch_sha'] != before['patch_sha']
    assert after['revision'] == before['revision'] + 1
    assert not current.metadata['delivery_approved']
    other = ALLOWED[1 - target_index]
    assert git(s.repo, 'show', after['candidate_commit'] + ':' + other) == initial_files[other]
    assert git(s.repo, 'show', after['candidate_commit'] + ':' + target) == initial_files[target] + '# bounded synthetic change\n'
    assert s.gateway.reviews == 2
    with pytest.raises(DeliveryConflict):
        c.export(rid, 'founder')
