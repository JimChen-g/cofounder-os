"""Synthetic delivery revalidation; these fixtures are not model approvals."""
from copy import deepcopy
import hashlib

import pytest

from app.services.engineering_delivery import DeliveryConflict, EngineeringDeliveryController


CHECKS = ('input_shape', 'material_rules', 'filenames', 'output_contract', 'side_effects', 'tests')


@pytest.fixture
def result():
    diff = 'synthetic diff used only for delivery revalidation'
    patch = hashlib.sha256(diff.encode()).hexdigest()
    return {
        'review_schema': 'engineering_review_v2',
        'state': 'passed_checks_pending_delivery_approval',
        'patch_sha': patch,
        'code_diff': diff,
        'tests': [{'patch_sha': patch, 'exit_code': 0, 'timed_out': False} for _ in range(3)],
        'executor': {'session_id': 'synthetic-implementation-session'},
        'reviewer': {'session_id': 'synthetic-independent-review-session'},
        'review': {
            'patch_sha': patch, 'conclusion': 'passed', 'findings': [],
            'checks': {name: {'satisfied': True, 'evidence': 'synthetic checked evidence'} for name in CHECKS},
        },
    }


def test_delivery_accepts_all_six_strict_v2_checks(result):
    EngineeringDeliveryController._checks(result)


@pytest.mark.parametrize('name', CHECKS)
def test_delivery_rejects_failed_v2_check_even_with_passed_conclusion(result, name):
    result['review']['checks'][name]['satisfied'] = False
    with pytest.raises(DeliveryConflict, match='checks_not_current'):
        EngineeringDeliveryController._checks(result)


@pytest.mark.parametrize('value', [1, 'true', 'passed', None, [], {}])
def test_delivery_does_not_coerce_v2_check_satisfied_value(result, value):
    result['review']['checks']['input_shape']['satisfied'] = value
    with pytest.raises(DeliveryConflict, match='checks_not_current'):
        EngineeringDeliveryController._checks(result)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'wrong_name', 'list', 'null', 'non_object_check'])
def test_delivery_rejects_non_exact_v2_check_structure(result, mutation):
    checks = result['review']['checks']
    if mutation == 'missing':
        del checks['tests']
    elif mutation == 'extra':
        checks['extra'] = {'satisfied': True}
    elif mutation == 'wrong_name':
        checks['test_honesty'] = checks.pop('tests')
    elif mutation == 'list':
        result['review']['checks'] = list(checks.values())
    elif mutation == 'null':
        result['review']['checks'] = None
    else:
        checks['tests'] = True
    with pytest.raises(DeliveryConflict, match='checks_not_current'):
        EngineeringDeliveryController._checks(result)


def test_delivery_v2_still_rejects_blocking_finding_and_stale_patch(result):
    blocking = deepcopy(result)
    blocking['review']['findings'] = [{'severity': 'blocking'}]
    stale = deepcopy(result)
    stale['review']['patch_sha'] = '0' * 64
    for invalid in (blocking, stale):
        with pytest.raises(DeliveryConflict, match='checks_not_current'):
            EngineeringDeliveryController._checks(invalid)


@pytest.mark.parametrize('schema', ['engineering_review_unknown', None])
def test_delivery_rejects_declared_unknown_or_null_review_schema(result, schema):
    result['review_schema'] = schema
    del result['review']['checks']
    with pytest.raises(DeliveryConflict, match='checks_not_current'):
        EngineeringDeliveryController._checks(result)


@pytest.mark.parametrize('schema', [None, 'engineering_review_v1'])
def test_delivery_preserves_pre_v2_historical_result_compatibility(result, schema):
    del result['review']['checks']
    if schema is None:
        del result['review_schema']
    else:
        result['review_schema'] = schema
    EngineeringDeliveryController._checks(result)
