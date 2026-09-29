"""Synthetic format recovery; never real model or human approval evidence."""
import json

import pytest

from app.models import Provider
from app.policy.request_policy import active_budget
from tests.test_engineering_delivery import env as delivery_env
from tests.test_engineering_execution import repo as engineering_repo

env = delivery_env
repo = engineering_repo


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['line', 'extra'])
async def test_review_format_recovery_keeps_candidate_and_original_evidence(env, failure):
    product, service, _ = env
    original = service.gateway.complete
    calls = []
    async def complete(messages, **kwargs):
        budget = active_budget.get()
        budget.reserve(Provider.QWEN, 100, kwargs['policy'])
        response = await original(messages, **kwargs)
        calls.append(messages)
        if len(calls) == 2:
            value = json.loads(response.content)
            if failure == 'line':
                value['checks']['tests']['line'] = 1
            else:
                value['unexpected'] = 'forbidden'
            response.content = json.dumps(value)
        return response
    service.gateway.complete = complete
    run = service.create('founder', 'synthetic-format-recovery').run
    outcome = await service.execute(run.id)
    assert outcome.status == 'waiting_approval'
    assert service.gateway.implementations == 1 and service.gateway.reviews == 2
    records = list(service.root.glob('*-evidence/result.json'))
    assert len(records) == 1
    result = json.loads(records[0].read_text())
    attempts = result['reviewer_attempts']
    assert len(attempts) == 2 and 'validation_error' in attempts[0]
    assert attempts[1]['validation_status'] == 'passed'
    if failure == 'line':
        diagnostics = attempts[0]['validation_error']['citation_diagnostics']
        assert any(item['check'] == 'tests' and item['exact_quote_lines'] for item in diagnostics)
        assert 'source_at_claimed_line' in attempts[1]['request']['messages'][-1]['content']
    assert attempts[0]['request']['messages'][:3] == attempts[1]['request']['messages'][:3]
    evidence = records[0].parent
    assert json.loads((evidence/'reviewer-response.json').read_text()) == attempts[0]['response']
    assert json.loads((evidence/'reviewer-response-format-retry.json').read_text()) == attempts[1]['response']
    assert attempts[0]['session_id'] != attempts[1]['session_id']
    with service.budgets.connect() as db:
        row = db.execute('SELECT attempts,tokens FROM budgets WHERE id=?', (run.metadata['engineering_budget_id'],)).fetchone()
    assert tuple(row) == (3, 300)
    assert not product.get_run(run.id).run.metadata.get('delivery_approved')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['always_invalid', 'changes_requested', 'rejection_with_extra', 'budget_exhausted'])
async def test_review_recovery_is_bounded_and_does_not_retry_semantic_rejection(env, kind):
    _, service, _ = env
    original = service.gateway.complete
    async def complete(messages, **kwargs):
        budget = active_budget.get()
        if kind == 'budget_exhausted' and 'mechanical format' in messages[-1].content:
            # Fill the SAME ledger before the recovery invocation, never replace it.
            with service.budgets.connect() as db:
                db.execute('UPDATE budgets SET attempts=4 WHERE id=?', (budget.key,))
        budget.reserve(Provider.QWEN, 100, kwargs['policy'])
        response = await original(messages, **kwargs)
        if 'independent code Reviewer' in messages[0].content:
            value = json.loads(response.content)
            value['checks']['tests']['line'] = 1
            if kind in {'changes_requested', 'rejection_with_extra'}:
                value['conclusion'] = 'changes_requested'
            if kind == 'rejection_with_extra':
                value['extra'] = True
            response.content = json.dumps(value)
        return response
    service.gateway.complete = complete
    run = service.create('founder', 'synthetic-bounded-format-failure').run
    assert (await service.execute(run.id)).status == 'failed'
    first = min((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')), key=lambda r:r['attempt'])
    assert first['state'] == 'failed'
    attempts = first['reviewer_attempts']
    assert len(attempts) == (1 if kind in {'changes_requested', 'rejection_with_extra'} else 2)
    if kind == 'budget_exhausted':
        assert attempts[1]['invocation_error'] == 'PolicyDenied'
        assert 'response' not in attempts[1]
    else:
        assert attempts[-1]['validation_error']['kind'] == (
            'review_schema_invalid' if kind == 'rejection_with_extra' else 'review_evidence_not_in_patch')
