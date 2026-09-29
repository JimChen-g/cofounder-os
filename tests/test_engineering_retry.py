"""Synthetic governance checks; the candidate code is never executed on the host."""
import json

import pytest

from app.clients.gateway import GatewayClient, GatewayCompletion
from app.config import Settings
from app.engineering.service import EngineeringService, TEST_FIXTURE_HELPER, numbered_source
from app.engineering.workspace import ALLOWED, Workspace, git
from app.services.product_api import build_product_api_service
from tests.test_engineering_execution import repo as engineering_repo_fixture
from tests.engineering_review_helpers import synthetic_review_checks

repo = engineering_repo_fixture


class RetryGateway(GatewayClient):
    def __init__(self, path, invalid_edit=None):
        super().__init__('http://invalid')
        self.path = path
        self.invalid_edit = invalid_edit
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if 'repairing failed checks' in messages[0].content:
            content = {'edits': [self.invalid_edit or {
                'path': self.path, 'old': '# broken\nsynthetic_fixture = True\n', 'new': '# repaired\nsynthetic_fixture = True\n'}]}
        elif 'implementation Agent' in messages[0].content:
            content = {'implementation': '# broken\nsynthetic_fixture = True\n' if self.path == ALLOWED[0] else '# preserve implementation\nsynthetic_fixture = True\n',
                       'tests': '# broken\nsynthetic_fixture = True\n' if self.path == ALLOWED[1] else '# preserve tests\nsynthetic_fixture = True\n'}
        else:
            content = {'checks': synthetic_review_checks(messages),
                       'patch_sha': json.loads(messages[1].content)['patch_sha'],
                       'conclusion': 'passed', 'findings': []}
        return GatewayCompletion(content=json.dumps(content), requested_model='synthetic')


def environment(repo, tmp_path, monkeypatch, path, invalid_edit=None):
    product = build_product_api_service(Settings(PRODUCT_DATA_DIR=str(tmp_path / 'data')))
    service = EngineeringService(product, repo, tmp_path / 'tasks')
    service.gateway = RetryGateway(path, invalid_edit)
    gates = []

    def test(workspace, argv, image):
        gates.append((workspace.id, argv))
        gate_targets_file = path == ALLOWED[0] or ALLOWED[1] in argv
        failed = gate_targets_file and '# broken' in (workspace.path / path).read_text()
        return {'exit_code': int(failed), 'timed_out': False,
                'patch_sha': workspace.verify(), 'log': 'synthetic fixture failure' if failed else 'synthetic pass',
                'gate': {'passed': not failed}, 'argv': argv,
                'cwd': 'synthetic-sandbox', 'duration_seconds': 0.001}

    monkeypatch.setattr(Workspace, 'test', test)
    return product, service, gates


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ALLOWED)
async def test_failed_check_retry_preserves_other_file_and_reruns_all_gates(repo, tmp_path, monkeypatch, path):
    product, service, gates = environment(repo, tmp_path, monkeypatch, path)
    run = service.create('founder', 'synthetic-bounded-retry').run
    result = await service.execute(run.id)
    assert result.status == 'waiting_approval'
    assert result.snapshot.tasks[0].attempt_count == 2
    records = sorted((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')),
                     key=lambda value: value['attempt'])
    failed, repaired = records
    assert failed['state'] == 'failed'
    assert repaired['retry_of']['candidate_commit'] == failed['candidate_commit']
    assert repaired['retry_of']['patch_sha'] == failed['patch_sha']
    assert repaired['patch_sha'] != failed['patch_sha']
    assert repaired['base_sha'] == failed['base_sha'] == run.metadata['base_sha']
    assert git(repo, 'rev-parse', repaired['candidate_commit'] + '^').strip() == repaired['base_sha']
    other = next(value for value in ALLOWED if value != path)
    assert git(repo, 'show', repaired['candidate_commit'] + ':' + other) == git(repo, 'show', failed['candidate_commit'] + ':' + other)
    assert git(repo, 'show', repaired['candidate_commit'] + ':' + path) == '# repaired\nsynthetic_fixture = True\n'
    assert len([workspace for workspace, _ in gates if workspace == repaired['workspace_id']]) == 3
    assert repaired['executor']['session_id'] != repaired['reviewer']['session_id']
    assert len(service.gateway.calls) == 3
    assert [kwargs['max_tokens'] for _, kwargs in service.gateway.calls] == [5500, 1600, 3000]
    assert 'synthetic fixture failure' in service.gateway.calls[1][0][1].content
    assert not product.get_run(run.id).run.metadata['delivery_approved']


@pytest.mark.asyncio
@pytest.mark.parametrize('edit', [
    {'path': '../outside.py', 'old': '# broken\nsynthetic_fixture = True\n', 'new': '# repaired\nsynthetic_fixture = True\n'},
    {'path': ALLOWED[0], 'old': '', 'new': '# repaired\nsynthetic_fixture = True\n'},
    {'path': ALLOWED[0], 'old': '# missing\n', 'new': '# repaired\nsynthetic_fixture = True\n'},
    {'path': ALLOWED[0], 'old': '# broken\nsynthetic_fixture = True\n', 'new': '# repaired\nsynthetic_fixture = True\n', 'command': 'ignored'},
])
async def test_invalid_retry_edit_fails_closed_without_more_checks_or_attempts(repo, tmp_path, monkeypatch, edit):
    product, service, gates = environment(repo, tmp_path, monkeypatch, ALLOWED[0], edit)
    run = service.create('founder', 'synthetic-invalid-retry').run
    result = await service.execute(run.id)
    assert result.status == 'failed'
    assert result.snapshot.tasks[0].attempt_count == 2
    assert len(service.gateway.calls) == 2
    assert len(gates) == 3
    assert 'delivery' not in product.get_run(run.id).run.metadata
    assert not (repo / 'outside.py').exists()


@pytest.mark.asyncio
async def test_reviewer_counterexample_is_bound_to_retry_and_independently_reviewed(repo, tmp_path, monkeypatch):
    product, service, _ = environment(repo, tmp_path, monkeypatch, ALLOWED[0])
    original_complete = service.gateway.complete
    reviews = []

    async def complete(messages, **kwargs):
        response = await original_complete(messages, **kwargs)
        if 'independent code Reviewer' in messages[0].content:
            reviews.append(json.loads(messages[1].content)['patch_sha'])
            if len(reviews) == 1:
                response.content = json.dumps({'checks': synthetic_review_checks(messages),
                    'patch_sha': reviews[-1], 'conclusion': 'changes_requested',
                    'findings': [{'path': ALLOWED[0], 'line': 1, 'severity': 'blocking',
                                  'trigger': 'synthetic counterexample', 'impact': 'wrong result',
                                  'evidence': 'synthetic exact actual versus required result'}]})
        return response

    service.gateway.complete = complete
    monkeypatch.setattr(Workspace, 'test', lambda workspace, argv, image: {
        'exit_code': 0, 'timed_out': False, 'patch_sha': workspace.verify(), 'argv': argv,
        'log': 'synthetic pass', 'gate': {'passed': True}, 'cwd': 'synthetic-sandbox',
        'duration_seconds': 0.001})
    run = service.create('founder', 'synthetic-review-retry').run
    result = await service.execute(run.id)
    assert result.status == 'waiting_approval'
    assert result.snapshot.tasks[0].attempt_count == 2
    assert len(reviews) == 2 and reviews[0] != reviews[1]
    repair_request = service.gateway.calls[2][0]
    assert 'repairing failed checks' in repair_request[0].content
    assert 'synthetic counterexample' in repair_request[1].content
    assert reviews[0] in repair_request[1].content
    assert not product.get_run(run.id).run.metadata['delivery_approved']


@pytest.mark.asyncio
async def test_one_retry_sees_and_repairs_independent_failures_from_all_gates(repo, tmp_path, monkeypatch):
    product, service, _ = environment(repo, tmp_path, monkeypatch, ALLOWED[0])
    calls = []

    async def complete(messages, **kwargs):
        calls.append(messages)
        if 'repairing failed checks' in messages[0].content:
            assert 'permuted_input' in messages[1].content
            assert 'duplicate filename in generated valid fixture' in messages[1].content
            content = {'edits': [
                {'path': ALLOWED[0], 'old': '# input order\nsynthetic_fixture = True\n', 'new': '# required order\nsynthetic_fixture = True\n'},
                {'path': ALLOWED[1], 'old': '# same filename\nsynthetic_fixture = True\n', 'new': '# unique filenames\nsynthetic_fixture = True\n'},
            ]}
        elif 'implementation Agent' in messages[0].content:
            content = {'implementation': '# input order\nsynthetic_fixture = True\n', 'tests': '# same filename\nsynthetic_fixture = True\n'}
        else:
            content = {'checks': synthetic_review_checks(messages),
                       'patch_sha': json.loads(messages[1].content)['patch_sha'],
                       'conclusion': 'passed', 'findings': []}
        return GatewayCompletion(content=json.dumps(content), requested_model='synthetic')

    def test(workspace, argv, image):
        if '/checks/accept_case.py' in argv:
            failed = '# input order' in (workspace.path / ALLOWED[0]).read_text()
            gate = {'passed': not failed, 'cases': [{'case_id': 'permuted_input', 'passed': not failed}]}
            log = 'synthetic ordering observation'
        elif ALLOWED[1] in argv:
            failed = '# same filename' in (workspace.path / ALLOWED[1]).read_text()
            gate = {'passed': not failed}
            log = 'duplicate filename in generated valid fixture'
        else:
            failed, gate, log = False, {'passed': True}, 'synthetic regression pass'
        return {'exit_code': int(failed), 'timed_out': False, 'patch_sha': workspace.verify(),
                'log': log, 'gate': gate, 'argv': argv, 'cwd': 'synthetic-sandbox', 'duration_seconds': 0.001}

    service.gateway.complete = complete
    monkeypatch.setattr(Workspace, 'test', test)
    run = service.create('founder', 'synthetic-two-defect-retry').run
    result = await service.execute(run.id)
    assert result.status == 'waiting_approval'
    assert result.snapshot.tasks[0].attempt_count == 2
    assert len(calls) == 3
    records = sorted((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')),
                     key=lambda value: value['attempt'])
    failed, repaired = records
    assert [gate['exit_code'] for gate in failed['tests']] == [1, 1, 0]
    assert [gate['exit_code'] for gate in repaired['tests']] == [0, 0, 0]
    assert 'reviewer' not in failed
    assert repaired['review']['conclusion'] == 'passed'
    assert not product.get_run(run.id).run.metadata['delivery_approved']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [
    {'cleanup_confirmed': False},
    {'cleanup_error': 'synthetic_stop_failure'},
    {'execution_error': 'synthetic_launch_failure'},
])
async def test_infrastructure_failure_stops_gate_collection_without_overwriting_cleanup(repo, tmp_path, monkeypatch, failure):
    product, service, _ = environment(repo, tmp_path, monkeypatch, ALLOWED[0])
    workspaces = []
    calls = []

    def test(workspace, argv, image):
        workspaces.append(workspace)
        calls.append((workspace.id, argv))
        workspace._cleanup_confirmed = False
        return {'exit_code': 125, 'timed_out': False, 'patch_sha': workspace.verify(),
                'log': 'synthetic infrastructure failure', 'gate': {'passed': False}, 'argv': argv,
                'cwd': 'synthetic-sandbox', 'duration_seconds': 0.001, **failure}

    monkeypatch.setattr(Workspace, 'test', test)
    run = service.create('founder', 'synthetic-infrastructure-failure').run
    result = await service.execute(run.id)
    assert result.status == 'failed'
    assert result.snapshot.tasks[0].attempt_count == 2
    assert len(calls) == 2
    assert all('/checks/accept_case.py' in argv for _, argv in calls)
    assert len({workspace_id for workspace_id, _ in calls}) == 2
    assert len(service.gateway.calls) == 2
    assert all('implementation Agent' in messages[0].content for messages, _ in service.gateway.calls)
    for workspace in workspaces:
        record = json.loads((workspace.evidence / 'result.json').read_text())
        assert len(record['tests']) == 1
        assert all(record['tests'][0][key] == value for key, value in failure.items())
        assert 'reviewer' not in record
        with pytest.raises(ValueError, match='workspace_not_owned_or_active'):
            workspace.cleanup()
    assert 'delivery' not in product.get_run(run.id).run.metadata
    assert not product.get_run(run.id).run.metadata['delivery_approved']


def test_numbered_source_preserves_escapes_blank_lines_and_original_line_numbers():
    source = 'def check(filename):\n\n    return "\\\\" in filename or "\\t" in filename\n# final line'
    formatted = numbered_source(ALLOWED[0], source)
    assert formatted.startswith('FILE app/insurance_poc/materials.py\n1 | def check(filename):\n2 | \n')
    assert '3 |     return "\\\\" in filename or "\\t" in filename\n' in formatted
    assert formatted.endswith('4 | # final line\nEND FILE\n')
    assert 'filename):\\n' not in formatted


@pytest.mark.asyncio
async def test_reviewer_receives_plain_numbered_immutable_sources_separate_from_metadata(repo, tmp_path, monkeypatch):
    product, service, _ = environment(repo, tmp_path, monkeypatch, ALLOWED[0])
    implementation = 'def check(filename):\n    if not filename.strip() or "/" in filename or "\\\\" in filename:\n        raise ValueError("invalid filename")\n'
    tests = '# synthetic fixture containing a literal \\t sequence\nsynthetic_fixture = True\n'
    original_complete = service.gateway.complete

    async def complete(messages, **kwargs):
        response = await original_complete(messages, **kwargs)
        if 'implementation Agent' in messages[0].content:
            response.content = json.dumps({'implementation': implementation, 'tests': tests})
        return response

    service.gateway.complete = complete
    run = service.create('founder', 'synthetic-review-source-format').run
    result = await service.execute(run.id)
    assert result.status == 'waiting_approval'
    assert len(service.gateway.calls) == 2
    review_messages = service.gateway.calls[1][0]
    metadata = json.loads(review_messages[1].content)
    assert 'diff' not in metadata
    assert len(metadata['tests']) == 3
    assert all(gate['exit_code'] == 0 for gate in metadata['tests'])
    assert len(review_messages) == 3
    assert review_messages[2].content == numbered_source(ALLOWED[0], implementation) + '\n' + numbered_source(ALLOWED[1], tests)
    record = json.loads(next(service.root.glob('*-evidence/result.json')).read_text())
    assert git(repo, 'show', record['candidate_commit'] + ':' + ALLOWED[0]) == implementation
    assert record['executor']['session_id'] != record['reviewer']['session_id']
    assert metadata['patch_sha'] == record['patch_sha'] == record['review']['patch_sha']
    assert not product.get_run(run.id).run.metadata['delivery_approved']


def test_public_spec_fixture_helper_has_valid_distinct_defaults_and_explicit_overrides():
    """Execute only the app-owned setup template, never model-generated code."""
    namespace = {}
    exec(TEST_FIXTURE_HELPER, namespace)
    material = namespace['material']
    entries = [material(index) for index in range(3)]
    assert [entry['material_id'] for entry in entries] == [
        'requirement_document', 'accident_scene_image', 'accident_damage_image']
    assert [entry['content_type'] for entry in entries] == ['application/pdf', 'image/png', 'image/png']
    assert len({entry['filename'] for entry in entries}) == 3
    assert material(0, 'override', 'wrong/mime') == {
        'material_id': 'requirement_document', 'filename': 'override', 'content_type': 'wrong/mime'}
    assert material(index=1, filename='', content_type='') == {
        'material_id': 'accident_scene_image', 'filename': '', 'content_type': ''}


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['missing', 'extra', 'wrong_line', 'wrong_evidence', 'blank_evidence', 'false', 'string_boolean'])
async def test_v2_review_cannot_pass_with_unbound_or_unsatisfied_checks(repo, tmp_path, monkeypatch, mode):
    product, service, _ = environment(repo, tmp_path, monkeypatch, ALLOWED[0])
    original_complete = service.gateway.complete

    async def complete(messages, **kwargs):
        response = await original_complete(messages, **kwargs)
        if 'independent code Reviewer' in messages[0].content:
            assert kwargs['response_schema'] == 'engineering_review_v2'
            review = json.loads(response.content)
            check = review['checks']['input_shape']
            if mode == 'missing':
                del review['checks']['tests']
            elif mode == 'extra':
                review['checks']['other'] = dict(check)
            elif mode == 'wrong_line':
                check['line'] = 999
            elif mode == 'wrong_evidence':
                check['evidence'] = '# invented source quotation'
            elif mode == 'blank_evidence':
                check['evidence'] = ' '
            elif mode == 'false':
                check['satisfied'] = False
            else:
                check['satisfied'] = 'true'
            response.content = json.dumps(review)
        return response

    service.gateway.complete = complete
    monkeypatch.setattr(Workspace, 'test', lambda workspace, argv, image: {
        'exit_code': 0, 'timed_out': False, 'patch_sha': workspace.verify(), 'argv': argv,
        'log': 'synthetic pass', 'gate': {'passed': True}, 'cwd': 'synthetic-sandbox',
        'duration_seconds': 0.001})
    run = service.create('founder', 'synthetic-invalid-v2-review').run
    result = await service.execute(run.id)
    assert result.status == 'failed'
    assert result.snapshot.tasks[0].attempt_count == 2
    # This synthetic gateway does not reserve budget; the separate ledger tests
    # enforce the real invocation cap. Each task attempt permits one format retry.
    assert len(service.gateway.calls) == (4 if mode == 'false' else 5)
    for path in service.root.glob('*-evidence/result.json'):
        record = json.loads(path.read_text())
        assert record['review_schema'] == 'engineering_review_v2'
        attempts = record['reviewer_attempts']
        assert len(attempts) == (1 if mode == 'false' else 2)
        if mode != 'false':
            assert all('validation_error' in attempt for attempt in attempts)
            assert attempts[0]['request']['messages'][:3] == attempts[1]['request']['messages'][:3]
        assert len(record['tests']) == 3
        assert all(gate['exit_code'] == 0 for gate in record['tests'])
    assert 'delivery' not in product.get_run(run.id).run.metadata
    assert not product.get_run(run.id).run.metadata['delivery_approved']


@pytest.mark.asyncio
async def test_truncated_review_is_labeled_as_adapter_failure_not_model_verdict(repo, tmp_path, monkeypatch):
    _, service, _ = environment(repo, tmp_path, monkeypatch, ALLOWED[0])
    original_complete = service.gateway.complete

    async def complete(messages, **kwargs):
        response = await original_complete(messages, **kwargs)
        if 'independent code Reviewer' in messages[0].content:
            response.content = '{"checks":'
            response.finish_reason = 'length'
        return response

    service.gateway.complete = complete
    monkeypatch.setattr(Workspace, 'test', lambda workspace, argv, image: {
        'exit_code': 0, 'timed_out': False, 'patch_sha': workspace.verify(), 'argv': argv,
        'log': 'synthetic pass', 'gate': {'passed': True}, 'cwd': 'synthetic-sandbox',
        'duration_seconds': 0.001})
    run = service.create('founder', 'synthetic-truncated-review').run
    result = await service.execute(run.id)
    assert result.status == 'failed'
    for path in service.root.glob('*-evidence/result.json'):
        record = json.loads(path.read_text())
        assert record['review_status_source'] == 'adapter_output_truncated'
        assert record['reviewer']['content'] == '{"checks":'
        assert record['reviewer']['finish_reason'] == 'length'
