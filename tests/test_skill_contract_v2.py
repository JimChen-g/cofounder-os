"""Public client/schema and corrected evaluation regressions; no live model calls."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import urllib.error

import jsonschema
import pytest

from app.decision.service import DecisionRequest, DecisionResponse, decide
from app.providers.registry import ProviderRegistry
from tests.test_spark_decide import FakeProvider

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/spark-decide'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_live_shape_and_demo_match_public_schema():
    schema = json.loads((SKILL / 'references/response.schema.json').read_text())
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    response = await decide(DecisionRequest(task='fixture', candidates=['local']), registry, 'fixture')
    jsonschema.validate(response.model_dump(mode='json'), schema)
    demo = load(SKILL / 'scripts/demo.py', 'skill_demo')
    handler = object.__new__(demo.Fixture)
    request = json.dumps({'candidates': ['human']}).encode()
    handler.headers = {'Authorization': 'Bearer fixture-only', 'Content-Length': str(len(request))}
    handler.rfile, handler.wfile = io.BytesIO(request), io.BytesIO()
    handler.send_response = lambda *_: None
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None
    handler.do_POST()
    jsonschema.validate(json.loads(handler.wfile.getvalue()), schema)
    assert set(DecisionResponse.model_fields) == set(schema['properties'])


def test_request_duplicate_and_oversize_evidence_rejected_by_schema():
    schema = json.loads((SKILL / 'references/request.schema.json').read_text())
    for request in ({'task': 'fixture', 'candidates': ['local', 'local']},
                    {'task': 'fixture', 'evidence_ids': ['x' * 161]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(request, schema)


@pytest.mark.parametrize('status,error_class', [(422, 'http_error'), (401, 'http_error'),
                                              (500, 'http_error'), (None, 'transport_error')])
def test_client_classifies_errors_and_corrected_scorer(monkeypatch, capsys, status, error_class):
    client = load(SKILL / 'scripts/decide.py', 'portable_client')
    harness = load(ROOT / 'evaluation/t28-v2/run_pairs.py', 'corrected_harness')
    case = next(c for c in json.loads((ROOT / 'evaluation/t28/cases.json').read_text())
                if c['kind'] == 'failure')
    class Opener:
        def open(self, *args, **kwargs):
            if status:
                raise urllib.error.HTTPError('http://secret.invalid', status, 'secret', {}, None)
            raise urllib.error.URLError('secret connection details')
    monkeypatch.setattr(client.urllib.request, 'build_opener', lambda *_: Opener())
    monkeypatch.setenv('SPARK_DECIDE_API_KEY', 'secret-token')
    monkeypatch.setattr(sys, 'argv', ['decide.py', '-', '--endpoint', 'http://127.0.0.1/v1/spark-decide'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(case['request'])))
    assert client.main() == 1
    output = capsys.readouterr().out
    assert 'secret' not in output
    receipt = json.loads(output)
    assert receipt['http_status'] == status and receipt['error_class'] == error_class
    score = harness.score(case, [{'kind': 'tool', 'exit_code': 1,
                                'request': case['request'], 'response': receipt}], case['expected'])
    assert score['passed'] is (status == 422)


def test_continuation_keeps_finished_failures_and_blocks_partial(tmp_path, monkeypatch):
    harness = load(ROOT / 'evaluation/t28-v2/run_pairs.py', 'run_pairs')
    monkeypatch.setitem(sys.modules, 'run_pairs', harness)
    continuation = load(ROOT / 'evaluation/t28-v2/continue_pairs.py', 'continuation')
    cases = [{'id': 'fixture'}]
    name, _, arm, attempt = next(continuation.planned_trials(cases))
    path = tmp_path / name
    path.write_text(json.dumps({'status': 'finished', 'case_id': 'fixture', 'arm': arm,
                               'attempt': attempt, 'score': {'passed': False}}))
    retained, missing = continuation.inventory(tmp_path, cases)
    assert name in retained and name not in missing and len(missing) == 3
    original = path.read_bytes()
    continuation.inventory(tmp_path, cases)
    assert path.read_bytes() == original
    path.write_text(json.dumps({'status': 'reserved'}))
    with pytest.raises(ValueError, match='manual_inspection'):
        continuation.inventory(tmp_path, cases)


def test_original_t28_freeze_bytes_preserved():
    import hashlib
    original = ROOT / 'evaluation/t28'
    frozen = json.loads((original / 'freeze.json').read_text())
    for relative, key in [('cases.json', 'cases_sha256'), ('run_pairs.py', 'harness_sha256'),
                          ('frozen-skill/SKILL.md', 'skill_sha256'),
                          ('frozen-skill/scripts/decide.py', 'script_sha256')]:
        assert hashlib.sha256((original / relative).read_bytes()).hexdigest() == frozen[key]
