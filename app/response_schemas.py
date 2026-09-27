"""Server-owned bounded response schemas; clients select names, never arbitrary schemas."""
from typing import Any, Literal

ResponseSchema = Literal['engineering_review_v1']


def response_format(name: ResponseSchema) -> dict[str, Any]:
    if name != 'engineering_review_v1':
        raise ValueError('unsupported_response_schema')
    short = {'type': 'string', 'minLength': 1, 'maxLength': 180}
    finding = {
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
