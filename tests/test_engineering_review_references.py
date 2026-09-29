"""Synthetic reference protocol tests, not live-model or founder approvals."""
from copy import deepcopy
import json

import pytest
from jsonschema import validate

from app.engineering.review_references import reference_table, resolve_review
from app.engineering.service import ReferenceReview, Review, validate_review_evidence
from app.engineering.workspace import ALLOWED
from app.response_schemas import response_format
from tests.test_engineering_delivery import env as delivery_env
from tests.test_engineering_execution import repo as engineering_repo

env = delivery_env
repo = engineering_repo
NAMES = ['input_shape', 'material_rules', 'filenames', 'output_contract', 'side_effects', 'tests']


def fixture():
    files = {ALLOWED[0]: 'import os\n# comment\nif "/" in name or "\\\\" in name:\n    raise ValueError()\n', ALLOWED[1]: 'assert actual == expected\n'}
    raw = {'patch_sha': 'a' * 64, 'checks': {name: {'ref': 'T1' if name == 'tests' else 'I3', 'satisfied': True} for name in NAMES}, 'findings': [], 'conclusion': 'passed'}
    return files, raw


def test_reference_resolves_literal_escapes_without_model_retyping():
    files, raw = fixture()
    before = deepcopy(raw)
    table = reference_table(files, raw['patch_sha'])
    validate(raw, response_format('engineering_review_v3')['json_schema']['schema'])
    wire = ReferenceReview.model_validate(raw)
    resolved = Review.model_validate(resolve_review(wire.model_dump(), table, files))
    validate_review_evidence(resolved, files)
    assert resolved.checks.filenames.evidence == 'if "/" in name or "\\\\" in name:'
    assert resolved.checks.filenames.satisfied is True
    assert raw == before
    assert 'I1' not in table['references'] and 'I2' not in table['references']


@pytest.mark.parametrize('mutation', ['unknown', 'import', 'comment', 'stale_patch', 'changed_source', 'tampered_table'])
def test_reference_fails_closed(mutation):
    files, raw = fixture()
    table = reference_table(files, raw['patch_sha'])
    if mutation in ['unknown', 'import', 'comment']:
        raw['checks']['filenames']['ref'] = {'unknown': 'I999', 'import': 'I1', 'comment': 'I2'}[mutation]
    elif mutation == 'stale_patch':
        raw['patch_sha'] = 'b' * 64
    elif mutation == 'changed_source':
        files[ALLOWED[0]] += '# altered\n'
    else:
        table['references']['I3']['evidence'] = 'altered'
    with pytest.raises(RuntimeError):
        resolve_review(raw, table, files)


@pytest.mark.parametrize('mutation', ['extra_quote', 'non_bool', 'missing_check'])
def test_wire_schema_does_not_accept_ambiguous_judgments(mutation):
    _, raw = fixture()
    if mutation == 'extra_quote':
        raw['checks']['filenames']['evidence'] = 'invented'
    elif mutation == 'non_bool':
        raw['checks']['filenames']['satisfied'] = 'true'
    else:
        del raw['checks']['tests']
    with pytest.raises(ValueError):
        ReferenceReview.model_validate(raw)


@pytest.mark.parametrize('blocking', [False, True])
async def test_reference_protocol_preserves_semantic_and_approval_gates(env, blocking):
    product, service, _ = env
    original = service.gateway.complete
    async def complete(messages, **kwargs):
        response = await original(messages, **kwargs)
        if kwargs['response_schema'] == 'engineering_review_v3':
            raw = json.loads(response.content)
            for check in raw['checks'].values():
                ref = ('I' if check['path'] == ALLOWED[0] else 'T') + str(check['line'])
                check.clear()
                check.update(ref=ref, satisfied=not blocking)
            if blocking:
                raw['conclusion'] = 'changes_requested'
                raw['findings'] = [{'ref': 'I2', 'trigger': 'synthetic wrong result', 'impact': 'violates contract', 'severity': 'blocking'}]
            response.content = json.dumps(raw)
        return response
    service.gateway.complete = complete
    run = service.create('founder', 'synthetic-ref-' + str(blocking)).run
    result = await service.execute(run.id)
    assert result.status == ('failed' if blocking else 'waiting_approval')
    assert not product.get_run(run.id).run.metadata.get('delivery_approved')
    records = [json.loads(p.read_text()) for p in service.root.glob('*-evidence/result.json')]
    reviewed = [r for r in records if 'reviewer_attempts' in r]
    assert reviewed and all(r['review_wire_schema'] == 'engineering_review_v3' for r in reviewed)
    assert reviewed[0]['reviewer_attempts'][0]['evidence_origin'] == 'host_resolved_patch_bound_references'
    assert list(service.root.glob('*-evidence/reviewer-references.json'))


def test_display_labels_only_eligible_exact_source_and_rejects_wrong_file():
    from app.engineering.review_references import referenced_source
    files, raw = fixture()
    table = reference_table(files, raw['patch_sha'])
    shown = referenced_source(ALLOWED[0], files[ALLOWED[0]], table)
    assert '1 | import os' in shown and '[I1]' not in shown
    assert '2 | # comment' in shown and '[I2]' not in shown
    assert '[I3] 3 | if "/" in name or "\\\\" in name:' in shown
    raw['checks']['tests']['ref'] = 'I3'
    with pytest.raises(RuntimeError, match='review_reference_invalid'):
        resolve_review(raw, table, files)
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        validate(raw, response_format('engineering_review_v3')['json_schema']['schema'])
