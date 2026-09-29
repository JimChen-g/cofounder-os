"""Retained model responses are data only; generated Python is never executed here."""
import json
from pathlib import Path

import pytest

from app.engineering.service import (Patch, RetryPatch, TEST_FIXTURE_HELPER,
                                     reject_runtime_noop, retry_messages, runtime_failures)

FIXTURE = json.loads((Path(__file__).parent/'fixtures/runtime_repair_failure.json').read_text())


def test_actual_tracebacks_retain_both_runtime_failures_without_warnings():
    failures = runtime_failures(FIXTURE['log'])
    assert [f['test'] for f in failures] == ['test_extra_material_key', 'test_too_many_materials']
    assert 'extra=1' in failures[0]['traceback'] and 'TypeError:' in failures[0]['exceptions']
    assert 'range(4)' in failures[1]['traceback'] and 'IndexError:' in failures[1]['exceptions']
    assert all('StarletteDeprecationWarning' not in f['traceback'] for f in failures)
    assert all(f['truncated'] == 'false' for f in failures)
    assert len(runtime_failures(FIXTURE['log']*20)) <= 4
    assert sum(len(f['traceback']) for f in runtime_failures(FIXTURE['log']*20)) <= 6000
    messages = retry_messages(Patch.model_validate(FIXTURE['files']).files,
                              [{'failed_checks': [{'runtime_failures': failures}]}])
    assert 'TypeError:' in messages[1].content and 'IndexError:' in messages[1].content
    assert '{**material(0), "extra": 1}' in messages[0].content
    assert 'outside pytest.raises' in messages[0].content


def test_actual_type_ignore_only_repair_is_rejected_but_source_change_is_not():
    before = Patch.model_validate(FIXTURE['files']).files
    after = dict(before)
    for edit in RetryPatch.model_validate(FIXTURE['repair']).edits:
        assert after[edit.path].count(edit.old) == 1
        after[edit.path] = after[edit.path].replace(edit.old, edit.new, 1)
    with pytest.raises(ValueError, match='runtime_repair_has_no_semantic_change'):
        reject_runtime_noop(before, after)
    tests = next(p for p in after if p.startswith('tests/'))
    after[tests] = before[tests].replace('material(0, filename="test", content_type="application/pdf", extra=1)',
                                       '{**material(0, filename="test", content_type="application/pdf"), "extra": 1}')
    after[tests] = after[tests].replace('[material(i) for i in range(4)]',
                                      '[material(i) for i in range(3)] + [material(0, filename="extra")]')
    reject_runtime_noop(before, after)


def test_guided_invalid_entries_reach_function_under_test():
    namespace = {}
    exec(TEST_FIXTURE_HELPER, namespace)  # trusted app-owned helper only
    material = namespace['material']
    payloads = [{'materials': [{**material(0), 'extra': 1}]},
                {'materials': [material(i) for i in range(3)] + [material(0, filename='extra')]}]
    received = []
    def function_under_test(payload):
        received.append(payload)
        raise ValueError('synthetic invalid input rejection')
    for payload in payloads:
        with pytest.raises(ValueError):
            function_under_test(payload)
    assert received == payloads
