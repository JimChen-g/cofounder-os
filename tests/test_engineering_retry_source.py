"""Regressions from retained live failures; execution below uses synthetic gates."""
import json

import pytest

from app.engineering.service import CONTRACT, TEST_FIXTURE_HELPER, retry_messages
from app.engineering.workspace import ALLOWED, Workspace
from tests.test_engineering_retry import environment, repo as engineering_repo

repo = engineering_repo

REAL_FAILED_RESPONSE = '{\n  "edits": [\n    {\n      "path": "tests\\/test_insurance_poc_materials.py",\n      "old": "def test_too_many_materials():",\n      "new": "def test_too_many_materials():  # noqa: F811"\n    },\n    {\n      "path": "tests\\/test_insurance_poc_materials.py",\n      "old": "    payload = {&quot;schema_version&quot;: &quot;insurance-materials-input-0.1&quot;, &quot;materials&quot;: [material(i) for i in range(4)]}",\n      "new": "    payload = {&quot;schema_version&quot;: &quot;insurance-materials-input-0.1&quot;, &quot;materials&quot;: [material(i) for i in range(3)] + [{&quot;material_id&quot;: IDS[0], &quot;filename&quot;: &quot;extra&quot;, &quot;content_type&quot;: MIMES[0]}]}"\n    }\n  ]\n}'
REAL_SOURCE_LINE = '    payload = {"schema_version": "insurance-materials-input-0.1", "materials": [material(i) for i in range(4)]}'


def test_retry_prompt_preserves_literal_source_and_failure_context():
    source = REAL_SOURCE_LINE + '\npath = "a\\b"\n'
    messages = retry_messages({ALLOWED[1]: source}, [{'error': 'IndexError'}])
    assert 'FILE ' + ALLOWED[1] + '\n' + source + 'END FILE' in messages[1].content
    assert 'IndexError' in messages[1].content
    assert 'JSON string escaping' in messages[0].content
    assert 'Never replace source quotes with HTML entities' in messages[0].content


def test_documented_overlength_fixture_builds_before_validation():
    namespace = {}
    exec(TEST_FIXTURE_HELPER.replace('index % len(IDS)', 'index').replace('index % len(MIMES)', 'index'), namespace)  # trusted app-owned helper, not generated code
    material = namespace['material']
    with pytest.raises(IndexError):
        [material(i) for i in range(4)]
    entries = [material(i) for i in range(3)] + [material(0, filename="extra")]
    assert len(entries) == 4 and all(set(item) == {'material_id', 'filename', 'content_type'} for item in entries)
    assert 'fixture construction only' in CONTRACT
    assert '[material(i) for i in range(3)] + [material(0, filename="extra")]' in CONTRACT


@pytest.mark.asyncio
@pytest.mark.parametrize('html_anchor', [True, False])
async def test_actual_html_entity_anchor_is_rejected_without_unescaping(repo, tmp_path, monkeypatch, html_anchor):
    # Use the actual failed response's old string. The corrected case explicitly
    # copies real source; the service never decodes entities on the model's behalf.
    edit = json.loads(REAL_FAILED_RESPONSE)['edits'][1]
    if not html_anchor:
        edit['old'] = REAL_SOURCE_LINE
    edit['new'] = '    payload = {"materials": []}'
    product, service, _ = environment(repo, tmp_path, monkeypatch, ALLOWED[1], edit)
    original = service.gateway.complete
    async def complete(messages, **kwargs):
        response = await original(messages, **kwargs)
        if 'implementation Agent' in messages[0].content and 'repairing failed checks' not in messages[0].content:
            response.content = json.dumps({'implementation': 'synthetic_fixture = True\n',
                                          'tests': 'def test_too_many_materials():\n' + REAL_SOURCE_LINE + '\n'})
        return response
    service.gateway.complete = complete
    monkeypatch.setattr(Workspace, 'test', lambda workspace, argv, image: {
        'exit_code': int(REAL_SOURCE_LINE in (workspace.path/ALLOWED[1]).read_text()),
        'timed_out': False, 'patch_sha': workspace.verify(), 'argv': argv,
        'log': 'synthetic regression gate', 'gate': {}, 'cwd': 'synthetic-sandbox', 'duration_seconds': 0.001})
    run = service.create('founder', 'synthetic-real-anchor-regression').run
    outcome = await service.execute(run.id)
    assert outcome.status == ('failed' if html_anchor else 'waiting_approval')
    records = sorted((json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')), key=lambda r:r['attempt'])
    assert len(records) == 2
    if html_anchor:
        assert records[1]['error'] == 'ValueError'
        assert 'candidate_commit' not in records[1]
        assert 'delivery' not in product.get_run(run.id).run.metadata
    else:
        assert records[1]['retry_of']['candidate_commit'] == records[0]['candidate_commit']
    assert not product.get_run(run.id).run.metadata['delivery_approved']
