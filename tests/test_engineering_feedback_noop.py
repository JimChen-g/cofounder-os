"""Synthetic feedback checks; preserve old candidates without restoring approval."""
import json

import pytest

from app.clients.gateway import GatewayCompletion
from app.models import Provider
from app.policy.request_policy import active_budget
from app.engineering.workspace import Workspace
from app.services.engineering_delivery import DeliveryConflict
from app.engineering.service import FeedbackEdit, apply_feedback_edit
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
        if kwargs['response_schema'] == 'engineering_feedback_v2':
            active_budget.get().reserve(Provider.QWEN, 100, kwargs['policy'])
            return GatewayCompletion(content=json.dumps({'operation':'replace',
                                     'old':'# synthetic revision 1', 'new':'# synthetic revision 1',
                                     'function_line':'', 'docstring':''}),
                                     requested_model='synthetic')
        return await original(messages, **kwargs)
    service.gateway.complete = complete
    outcome = await service.execute(rid)
    assert outcome.status == 'failed'
    assert calls == ['engineering_feedback_v2']
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
        elif kwargs['response_schema'] == 'engineering_feedback_v2':
            response.content = json.dumps({'operation':'replace', 'old':'Original documentation.',
                                           'new':'Clearer documentation.', 'function_line':'', 'docstring':''})
        return response
    service.gateway.complete = complete
    rid = await ready(env)
    controller.act(rid, 'founder', 'feedback', feedback(product, rid))
    assert (await service.execute(rid)).status == 'waiting_approval'
    assert product.get_run(rid).run.metadata['delivery']['revision'] == 2
    assert not product.get_run(rid).run.metadata['delivery_approved']


def test_host_renders_function_docstring_as_valid_indented_python():
    source = ('def check_material_completeness(payload: dict) -> dict:\n'
              '    return {"status": "complete"}\n')
    edit = FeedbackEdit(operation='insert_function_docstring', old='', new='',
                        function_line='def check_material_completeness(payload: dict) -> dict:',
                        docstring=('Accepts the insurance materials payload. Returns complete or incomplete '
                                   'material IDs. Raises ValueError for invalid input.'))
    updated, evidence = apply_feedback_edit(source, 'app/insurance_poc/materials.py', edit)
    namespace = {}
    exec(updated, namespace)
    function = namespace['check_material_completeness']
    assert function({}) == {'status': 'complete'}
    assert function.__doc__ == edit.docstring
    assert updated.splitlines()[1].startswith('    ')
    assert evidence['operation'] == 'insert_function_docstring'


@pytest.mark.parametrize('mutation', ['wrong_path', 'not_def', 'existing', 'mixed', 'empty'])
def test_docstring_operation_fails_closed(mutation):
    path = 'app/insurance_poc/materials.py'
    source = 'def check_material_completeness(payload: dict) -> dict:\n    return {}\n'
    values = {'operation': 'insert_function_docstring', 'old': '', 'new': '',
              'function_line': 'def check_material_completeness(payload: dict) -> dict:',
              'docstring': 'Documents input, output, and ValueError.'}
    if mutation == 'wrong_path':
        path = 'tests/test_insurance_poc_materials.py'
    elif mutation == 'not_def':
        values['function_line'] = '    return {}'
    elif mutation == 'existing':
        source = 'def check_material_completeness(payload: dict) -> dict:\n    """Existing."""\n    return {}\n'
    elif mutation == 'mixed':
        values['old'] = 'return {}'
    else:
        values['docstring'] = '   '
    with pytest.raises(ValueError):
        apply_feedback_edit(source, path, FeedbackEdit.model_validate(values))


@pytest.mark.asyncio
async def test_docstring_feedback_creates_checked_revision_two(env):
    product, service, controller = env
    original = service.gateway.complete

    async def complete(messages, **kwargs):
        response = await original(messages, **kwargs)
        if kwargs['response_schema'] == 'engineering_patch_v1':
            value = json.loads(response.content)
            value['implementation'] = ('def check_material_completeness(payload: dict) -> dict:\n'
                                       '    return {"status": "complete"}\n')
            response.content = json.dumps(value)
        elif kwargs['response_schema'] == 'engineering_feedback_v2':
            response.content = json.dumps({
                'operation': 'insert_function_docstring', 'old': '', 'new': '',
                'function_line': 'def check_material_completeness(payload: dict) -> dict:',
                'docstring': ('Input uses the insurance materials schema. Returns complete or incomplete '
                              'material IDs. Raises ValueError for invalid input.'),
            })
        return response

    service.gateway.complete = complete
    rid = await ready(env)
    controller.act(rid, 'founder', 'feedback', feedback(product, rid))
    outcome = await service.execute(rid)
    assert outcome.status == 'waiting_approval'
    snapshot = product.get_run(rid)
    assert snapshot.run.metadata['delivery']['revision'] == 2
    records = sorted((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')),
                     key=lambda result: result['attempt'])
    assert records[-1]['feedback_application']['operation'] == 'insert_function_docstring'
    assert records[-1]['state'] == 'passed_checks_pending_delivery_approval'
    assert all(check['exit_code'] == 0 for check in records[-1]['tests'])
    assert records[-1]['review']['conclusion'] == 'passed'
    assert not snapshot.run.metadata['delivery_approved']
