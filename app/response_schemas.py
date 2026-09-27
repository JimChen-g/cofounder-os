"""Server-owned bounded response schemas; clients select names, never arbitrary schemas."""
from typing import Any, Literal

ResponseSchema = Literal['engineering_review_v1', 'engineering_review_v2']


def response_format(name: ResponseSchema) -> dict[str, Any]:
    if name not in ('engineering_review_v1', 'engineering_review_v2'):
        raise ValueError('unsupported_response_schema')
    short = {'type': 'string', 'minLength': 1, 'maxLength': 180}
    finding: dict[str, Any] = {
        'type': 'object', 'additionalProperties': False,
        'required': ['path', 'line', 'trigger', 'impact', 'evidence', 'severity'],
        'properties': {
            'path': {'type': 'string', 'enum': ['app/insurance_poc/materials.py',
                                               'tests/test_insurance_poc_materials.py']},
            'line': {'type': 'integer', 'minimum': 1},
            'trigger': dict(short), 'impact': dict(short), 'evidence': dict(short),
            'severity': {'type': 'string', 'enum': ['blocking', 'warning', 'info']},
        },
    }
    if name == 'engineering_review_v2':
        check_names = ['input_shape', 'material_rules', 'filenames', 'output_contract',
                       'side_effects', 'tests']
        check = {
            'type': 'object', 'additionalProperties': False,
            'required': ['path', 'line', 'evidence', 'satisfied'],
            'properties': {
                'path': dict(finding['properties']['path']),
                'line': {'type': 'integer', 'minimum': 1},
                'evidence': dict(short),
                'satisfied': {'type': 'boolean'},
            },
        }
        return {'type': 'json_schema', 'json_schema': {
            'name': name, 'strict': True, 'schema': {
                'type': 'object', 'additionalProperties': False,
                'required': ['checks', 'patch_sha', 'findings', 'conclusion'],
                'properties': {
                    'checks': {
                        'type': 'object', 'additionalProperties': False,
                        'required': check_names,
                        'properties': {key: check for key in check_names},
                    },
                    'patch_sha': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                    'findings': {'type': 'array', 'maxItems': 3, 'items': finding},
                    'conclusion': {'type': 'string', 'enum': ['passed', 'changes_requested', 'inconclusive']},
                },
            },
        }}
    return {'type': 'json_schema', 'json_schema': {
        'name': name, 'strict': True, 'schema': {
            'type': 'object', 'additionalProperties': False,
            'required': ['patch_sha', 'conclusion', 'findings'],
            'properties': {
                'patch_sha': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                'conclusion': {'type': 'string', 'enum': ['passed', 'changes_requested', 'inconclusive']},
                'findings': {'type': 'array', 'maxItems': 3, 'items': finding},
            },
        },
    }}
