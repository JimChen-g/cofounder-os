"""CPU fixture tests only; these are never live A/B evidence."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('pairs', Path(__file__).with_name('run_pairs.py'))
assert spec is not None and spec.loader is not None
pairs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pairs)


class HarnessTests(unittest.TestCase):
    def test_negative_exact_without_tool(self):
        case = pairs.cases()[2]
        self.assertTrue(pairs.score(case, [], {'answer': 4})['passed'])
        self.assertFalse(pairs.score(case, [{'kind': 'tool'}], {'answer': 4})['passed'])

    def test_positive_requires_actual_receipt(self):
        case = pairs.cases()[0]
        self.assertFalse(pairs.score(case, [], {'action': 'local'})['passed'])
        row = {'kind': 'tool', 'exit_code': 0, 'request': case['request'],
               'response': {'action': 'local', 'decision_id': 'fixture-only'}}
        self.assertTrue(pairs.score(case, [row], {'action': 'local'})['passed'])
        row['exit_code'] = 1
        self.assertFalse(pairs.score(case, [row], {'action': 'local'})['passed'])

    def test_protocol_and_arm_equivalence(self):
        from app.request_constraints import RequestPolicy
        case = pairs.cases()[0]
        seen = []

        class Client:
            def __init__(self):
                self.calls = 0

            async def complete(self, messages, **kwargs):
                seen.append((messages[0].content, kwargs))
                self.calls += 1
                result = ({'tool': 'spark-decide', 'request': case['request']} if self.calls == 1
                          else {'final': {'action': 'local'}})
                return SimpleNamespace(content=json.dumps(result), usage={'total_tokens': 1},
                    selected_provider='qwen', selected_model='fixture-model', request_id='fixture', fallback_used=False)

        async def fake_tool(request, timeout):
            return {'kind': 'tool', 'exit_code': 0, 'request': request,
                    'response': {'action': 'local', 'decision_id': 'fixture-only'}}

        original = pairs.tool_call
        pairs.tool_call = fake_tool
        try:
            with tempfile.TemporaryDirectory() as temporary:
                policy = RequestPolicy(privacy='public', max_attempts=2, timeout_seconds=150)
                for arm in ('without_skill', 'with_skill'):
                    result = asyncio.run(pairs.trial(case, arm, Client(), policy, 'GUIDE', Path(temporary)/arm))
                    self.assertTrue(result['score']['passed'])
                    self.assertEqual(len(result['trace']), 3)
                self.assertEqual(seen[0][1].keys(), seen[2][1].keys())
                self.assertEqual(seen[0][0], pairs.PROTOCOL)
                self.assertEqual(seen[2][0], pairs.PROTOCOL + '\n\nAvailable Skill instructions:\nGUIDE')
        finally:
            pairs.tool_call = original

    def test_unauthorized_arguments_do_not_execute(self):
        from app.request_constraints import RequestPolicy
        class Client:
            async def complete(self, *args, **kwargs):
                return SimpleNamespace(content='{"tool":"spark-decide","request":{"candidates":["step"]}}',
                    usage={}, selected_provider='qwen', selected_model='fixture-model', request_id='fixture', fallback_used=False)
        with tempfile.TemporaryDirectory() as temporary:
            result = asyncio.run(pairs.trial(pairs.cases()[0], 'without_skill', Client(),
                RequestPolicy(privacy='public'), '', Path(temporary)/'result'))
            self.assertEqual(result['error'], 'tool_arguments_not_authorized')
            self.assertEqual(result['score']['tool_calls'], 0)
            self.assertFalse(result['score']['passed'])


if __name__ == '__main__':
    unittest.main()
