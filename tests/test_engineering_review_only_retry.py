"""Synthetic replay of format/transport failures; no live-model acceptance."""
import json

import pytest

from app.clients.gateway import GatewayClientError
from app.models import Provider
from app.policy.request_policy import active_budget
from tests.test_engineering_delivery import env as delivery_env
from tests.test_engineering_execution import repo as engineering_repo

env = delivery_env
repo = engineering_repo


@pytest.mark.asyncio
@pytest.mark.parametrize('exhaust', [False, True])
async def test_review_transport_retry_never_regenerates_tested_candidate(env, exhaust):
    product, service, _ = env
    original = service.gateway.complete
    review_calls = 0
    async def complete(messages, **kwargs):
        nonlocal review_calls
        budget = active_budget.get()
        budget.reserve(Provider.QWEN, 100, kwargs['policy'])
        if 'independent code Reviewer' in messages[0].content:
            review_calls += 1
            if review_calls == 2:
                if exhaust:
                    with service.budgets.connect() as db:
                        db.execute('UPDATE budgets SET attempts=4 WHERE id=?', (budget.key,))
                raise GatewayClientError('synthetic transport error')
        response = await original(messages, **kwargs)
        if review_calls == 1:
            value = json.loads(response.content)
            value['checks']['tests']['evidence'] = '&quot;invalid citation&quot;'
            value['patch_sha'] = 'wrong'
            response.content = json.dumps(value)
        return response
    service.gateway.complete = complete
    run = service.create('founder', 'synthetic-review-only-retry').run
    outcome = await service.execute(run.id)
    assert outcome.status == ('failed' if exhaust else 'waiting_approval')
    records = sorted((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')), key=lambda r:r['attempt'])
    assert len(records) == 2
    assert service.gateway.implementations == 1
    first, second = records
    assert first['state'] == 'failed'
    assert first['reviewer_attempts'][0]['validation_error']['kind'] == 'review_evidence_not_in_patch'
    assert first['reviewer_attempts'][1]['invocation_error'] == 'GatewayClientError'
    retry_messages = first['reviewer_attempts'][1]['request']['messages']
    assert retry_messages[:3] == first['reviewer_attempts'][0]['request']['messages']
    assert first['reviewer_attempts'][0]['response']['content'] not in retry_messages[-1]['content']
    assert len(retry_messages[-1]['content']) < 5000
    assert 'validation_error' in retry_messages[-1]['content']
    assert second['review_retry_of']['candidate_commit'] == first['candidate_commit']
    assert second['patch_sha'] == first['patch_sha']
    assert second['executor']['skipped']
    assert len(second['tests']) == 3 and all(t['exit_code'] == 0 for t in second['tests'])
    with service.budgets.connect() as db:
        row = db.execute('SELECT attempts FROM budgets WHERE id=?', (run.metadata['engineering_budget_id'],)).fetchone()
    assert row[0] == 4
    assert review_calls == (2 if exhaust else 3)
    assert not product.get_run(run.id).run.metadata['delivery_approved']


@pytest.mark.asyncio
async def test_review_only_retry_refuses_changed_candidate_source(env, monkeypatch):
    from app.engineering import service as module
    _, service, _ = env
    original = service.gateway.complete
    original_git = module.git
    reviews = 0
    def changed_git(repo, *args):
        value = original_git(repo, *args)
        if reviews >= 2 and args[0] == 'show':
            return value + '\n# synthetic tampered immutable source\n'
        return value
    monkeypatch.setattr(module, 'git', changed_git)
    async def complete(messages, **kwargs):
        nonlocal reviews
        if 'independent code Reviewer' in messages[0].content:
            reviews += 1
            if reviews == 2:
                raise GatewayClientError('synthetic lost review response')
        response = await original(messages, **kwargs)
        if reviews == 1:
            value = json.loads(response.content)
            value['checks']['tests']['line'] = 999
            response.content = json.dumps(value)
        return response
    service.gateway.complete = complete
    run = service.create('founder', 'synthetic-changed-review-candidate').run
    assert (await service.execute(run.id)).status == 'failed'
    records = sorted((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')), key=lambda r:r['attempt'])
    assert len(records) == 2
    assert service.gateway.implementations == 1 and reviews == 2
    assert 'candidate_commit' not in records[1]
    assert 'tests' not in records[1] and 'reviewer_attempts' not in records[1]
    assert records[1]['state'] == 'failed'
