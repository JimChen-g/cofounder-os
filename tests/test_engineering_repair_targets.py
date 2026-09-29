"""Retained responses are parsed as data, never executed as host Python."""
import json
from pathlib import Path

import pytest

from app.engineering.repair_targets import (StatementRepair, apply_statements,
                                           independent_edits, statement_targets)
from app.engineering.service import Patch, runtime_failures, TEST_FIXTURE_HELPER
from app.response_schemas import response_format


def fixture(name='runtime_statement_failure.json'):
    value = json.loads((Path(__file__).parent/'fixtures'/name).read_text())
    files = Patch.model_validate(value['files']).files
    targets = statement_targets(files, runtime_failures(value['log']))
    return value, files, targets


def replacements(targets):
    return [{'target_id': t.target_id, 'new': t.old.replace('[material(i) for i in range(4)]',
            '[material(i) for i in range(3)] + [material(0, filename="extra")]').replace(
            'material(0, filename="test", content_type="application/pdf", extra=1)',
            '{**material(0, filename="test", content_type="application/pdf"), "extra": 1}')} for t in targets]


@pytest.mark.parametrize('name,count', [('runtime_statement_failure.json', 1), ('runtime_repair_failure.json', 2)])
def test_live_fixture_targets_only_failing_payload_assignments(name, count):
    _, files, targets = fixture(name)
    assert len(targets) == count
    assert all(t.old.lstrip().startswith('payload =') for t in targets)
    assert all('def test_' not in t.old for t in targets)
    result = apply_statements(files, targets, StatementRepair(edits=replacements(targets)))
    path = targets[0].path
    assert 'range(4)' not in result[path]
    # All text outside target spans is untouched, including assertions and raises.
    original_tail = files[path][targets[-1].end:]
    assert result[path].endswith(original_tail)


@pytest.mark.parametrize('mode', ['unknown', 'duplicate', 'noop', 'comment', 'def', 'multiple', 'raises', 'skip', 'var', 'source', 'dedent'])
def test_statement_protocol_rejects_invalid_or_escaping_repairs(mode):
    _, files, targets = fixture()
    edits = replacements(targets)
    if mode == 'unknown':
        edits[0]['target_id'] = 'target-2'
    elif mode == 'duplicate':
        edits.append(dict(edits[0]))
    elif mode == 'noop':
        edits[0]['new'] = targets[0].old
    elif mode == 'comment':
        edits[0]['new'] += '  # explanatory text'
    elif mode == 'def':
        edits[0]['new'] = '    def test_other(): pass'
    elif mode == 'multiple':
        edits[0]['new'] += '\n    pytest.skip()'
    elif mode == 'raises':
        edits[0]['new'] = '    with pytest.raises(IndexError): pass'
    elif mode == 'skip':
        edits[0]['new'] = '    payload = pytest.skip()'
    elif mode == 'dedent':
        edits[0]['new'] = 'payload = {}'
    elif mode == 'var':
        edits[0]['new'] = '    other = {}'
    else:
        files[targets[0].path] += '\n# changed source\n'
    with pytest.raises(ValueError):
        apply_statements(files, targets, StatementRepair(edits=edits))


def test_real_chained_edits_and_overlaps_fail_against_immutable_source():
    value, files, _ = fixture()
    with pytest.raises(ValueError, match='repair_anchor_not_unique'):
        independent_edits(files, value['repair']['edits'])
    with pytest.raises(ValueError, match='repair_anchors_overlap'):
        independent_edits({'file':'abcdef'}, [{'path':'file','old':'abc','new':'x'}, {'path':'file','old':'bc','new':'y'}])
    assert independent_edits({'file':'abcdef'}, [{'path':'file','old':'ab','new':'x'}, {'path':'file','old':'ef','new':'y'}]) == {'file':'xcd y'.replace(' ','')}


def test_generic_docstring_change_remains_supported_and_schema_lengths_match():
    assert independent_edits({'file':'"""old docs"""\nx=1\n'}, [{'path':'file','old':'old docs','new':'new docs'}])['file'].startswith('"""new docs')
    schema = response_format('engineering_retry_v1')['json_schema']['schema']
    assert schema['properties']['edits']['items']['properties']['new']['maxLength'] == 4000
    statement = response_format('engineering_statement_v1')['json_schema']['schema']
    assert statement['properties']['edits']['items']['properties']['new']['maxLength'] == 4000


def test_current_helper_builds_four_valid_entries_without_changing_sut_contract():
    namespace = {}
    exec(TEST_FIXTURE_HELPER, namespace)  # trusted app helper, not generated source
    entries = [namespace['material'](i) for i in range(4)]
    assert len(entries) == 4
    assert entries[0]['material_id'] == entries[3]['material_id']
    assert entries[0]['content_type'] == entries[3]['content_type']
    assert len({e['filename'] for e in entries}) == 4


@pytest.mark.asyncio
async def test_real_failure_routes_to_statement_schema_and_preserves_assertions(tmp_path, monkeypatch):
    from tests.test_engineering_execution import repo as repo_fixture
    from tests.test_engineering_retry import environment
    from app.clients.gateway import GatewayCompletion
    from app.engineering.workspace import ALLOWED, Workspace
    # Reuse the real repository fixture's setup; it only creates trusted base files.
    repository = repo_fixture.__wrapped__(tmp_path)
    value, files, targets = fixture()
    product, service, _ = environment(repository, tmp_path, monkeypatch, ALLOWED[1])
    original = service.gateway.complete
    calls = []
    async def complete(messages, **kwargs):
        calls.append(kwargs['response_schema'])
        if kwargs['response_schema'] == 'engineering_patch_v1':
            return GatewayCompletion(content=json.dumps(value['files']), requested_model='synthetic')
        if kwargs['response_schema'] == 'engineering_statement_v1':
            assert 'no new scaffold is installed' in messages[0].content
            assert 'cycles through' not in messages[0].content
            assert targets[0].old in messages[1].content or json.dumps(targets[0].old)[1:-1] in messages[1].content
            return GatewayCompletion(content=json.dumps({'edits': replacements(targets)}), requested_model='synthetic')
        return await original(messages, **kwargs)
    service.gateway.complete = complete
    monkeypatch.setattr(Workspace, 'test', lambda workspace, argv, image: {
        'exit_code': int('range(4)' in (workspace.path/ALLOWED[1]).read_text() and ALLOWED[1] in argv),
        'timed_out': False, 'patch_sha': workspace.verify(), 'argv': argv,
        'log': value['log'], 'gate': {}, 'cwd': 'synthetic-sandbox', 'duration_seconds': 0.001})
    run = service.create('founder', 'synthetic-statement-protocol').run
    assert (await service.execute(run.id)).status == 'waiting_approval'
    assert calls == ['engineering_patch_v1', 'engineering_statement_v1', 'engineering_review_v3']
    records = sorted((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')), key=lambda r:r['attempt'])
    assert len(records) == 2 and len(records[1]['tests']) == 3
    assert records[1]['runtime_repair_targets'][0]['source_sha256'] == targets[0].source_sha256
    assert not product.get_run(run.id).run.metadata['delivery_approved']
