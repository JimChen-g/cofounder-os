"""Synthetic feedback checks; preserve old candidates without restoring approval."""
import json

import pytest

from app.clients.gateway import GatewayCompletion
from app.models import Provider
from app.policy.request_policy import active_budget
from app.engineering.workspace import Workspace
from app.services.engineering_delivery import DeliveryConflict
from tests.test_engineering_delivery import (env as delivery_env, ready, feedback, action)
from tests.test_engineering_execution import repo as engineering_repo

env = delivery_env
repo = engineering_repo


@pytest.mark.asyncio
async def test_feedback_noop_fails_before_candidate_tests_review_and_retains_history(env, monkeypatch):
    product, service, controller = env
    rid = await ready(env)
    before = product.get_run(rid).run.metadata['delivery'].copy()
    key = product.get_run(rid).run.metadata['engineering_budget_id']
    with service.budgets.connect() as db:
        budget_before = tuple(db.execute('SELECT attempts,tokens,started FROM budgets WHERE id=?', (key,)).fetchone())
    def forbidden(*args, **kwargs):
        raise AssertionError('no-op must stop before apply or tests')
    monkeypatch.setattr(Workspace, 'apply_files', forbidden)
    monkeypatch.setattr(Workspace, 'test', forbidden)
    body = feedback(product, rid)
    controller.act(rid, 'founder', 'feedback', body)
    original = service.gateway.complete
    calls = []
    async def complete(messages, **kwargs):
        calls.append(kwargs['response_schema'])
        if kwargs['response_schema'] == 'engineering_repair_v1':
            active_budget.get().reserve(Provider.QWEN, 100, kwargs['policy'])
            return GatewayCompletion(content=json.dumps({'old':'# synthetic revision 1', 'new':'# synthetic revision 1'}),
                                     requested_model='synthetic')
        return await original(messages, **kwargs)
    service.gateway.complete = complete
    outcome = await service.execute(rid)
    assert outcome.status == 'failed'
    assert calls == ['engineering_repair_v1']
    with service.budgets.connect() as db:
        budget_after = tuple(db.execute('SELECT attempts,tokens,started FROM budgets WHERE id=?', (key,)).fetchone())
    assert budget_after == (budget_before[0] + 1, budget_before[1] + 100, budget_before[2])
    records = sorted((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')), key=lambda r:r['attempt'])
    assert len(records) == 2
    failed = records[-1]
    assert failed['termination_reason'] == 'feedback_no_source_change'
    assert 'tests' not in failed and 'reviewer' not in failed and 'candidate_commit' not in failed
    snapshot = product.get_run(rid)
    assert snapshot.run.metadata['termination_reason'] == 'feedback_no_source_change'
    d = snapshot.run.metadata['delivery']
    assert d['state'] == 'repair_failed' and d['revision'] == before['revision']
    assert d['history'][-1]['artifact_id'] == before['artifact_id']
    assert snapshot.tasks[0].metadata['previous_result']['candidate_commit'] == records[0]['candidate_commit']
    assert not snapshot.run.metadata['delivery_approved']
    with pytest.raises(DeliveryConflict):
        controller.act(rid, 'founder', 'approve', action(product, rid))


@pytest.mark.asyncio
async def test_actual_docstring_text_change_is_not_rejected_as_ast_noop(env):
    product, service, controller = env
    original = service.gateway.complete
    async def complete(messages, **kwargs):
        response = await original(messages, **kwargs)
        if kwargs['response_schema'] == 'engineering_patch_v1':
            value = json.loads(response.content)
            value['implementation'] = '"""Original documentation."""\nsynthetic_fixture = True\n'
            response.content = json.dumps(value)
        elif kwargs['response_schema'] == 'engineering_repair_v1':
            response.content = json.dumps({'old':'Original documentation.', 'new':'Clearer documentation.'})
        return response
    service.gateway.complete = complete
    rid = await ready(env)
    controller.act(rid, 'founder', 'feedback', feedback(product, rid))
    assert (await service.execute(rid)).status == 'waiting_approval'
    assert product.get_run(rid).run.metadata['delivery']['revision'] == 2
    assert not product.get_run(rid).run.metadata['delivery_approved']
