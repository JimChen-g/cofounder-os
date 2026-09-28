#!/usr/bin/env python3
"""Bounded project A/B harness; NOT official NVIDIA Tier3 or an open-shell agent."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
SKILL = ROOT / 'skills/spark-decide'
SCORER_VERSION = 't28-http-contract-v2'
PROTOCOL = '''You are testing a bounded routing interface. Output exactly one JSON object per turn.
Available tool in ALL trials: spark-decide. It accepts the exact request object supplied in a routing task and returns an authenticated decision receipt. You may invoke it at most once.
To call: {"tool":"spark-decide","request":<the supplied request object>}.
To finish: {"final":<the requested answer object>}.
Use the decision tool when a task asks you to obtain a routing decision, preserve the supplied policy, and never execute its selected downstream action. Tasks without a routing choice need no tool. If the tool fails, finish with action refuse; do not retry or invent a receipt.
There are at most two model turns. After a tool result, finish. No shell, filesystem, network, or other tools are available. Do not claim probability scores or human approval.'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def score(case, trace, final):
    calls = [row for row in trace if row['kind'] == 'tool']
    correct = type(final) is dict and final == case['expected']
    if case['kind'] == 'negative':
        tool_ok = not calls
    elif case['kind'] == 'failure':
        tool_ok = (len(calls) == 1 and calls[0]['exit_code'] == 1
                   and calls[0].get('response', {}).get('error_class') == 'http_error'
                   and calls[0].get('response', {}).get('http_status') == 422
                   and calls[0].get('request') == case['request'])
    else:
        tool_ok = (len(calls) == 1 and calls[0]['exit_code'] == 0
                   and calls[0].get('response', {}).get('action') == case['expected']['action']
                   and bool(calls[0].get('response', {}).get('decision_id'))
                   and calls[0].get('request') == case['request'])
    return {'scorer_version': SCORER_VERSION, 'passed': correct and tool_ok,
            'exact_final': correct, 'tool_evidence_valid': tool_ok, 'tool_calls': len(calls)}


def cases():
    content = (HERE / 'cases.json').read_bytes()
    frozen = json.loads((HERE / 'freeze.json').read_text())
    if digest(content) != frozen['cases_sha256']:
        raise ValueError('case_freeze_changed')
    return json.loads(content)


async def tool_call(request, timeout):
    # Both arms execute the SAME portable script. No model-supplied shell is executed.
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(sys.executable, str(SKILL / 'scripts/decide.py'), '-',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(HERE), env={k: v for k, v in os.environ.items() if k in {
            'PATH', 'SPARK_DECIDE_URL', 'SPARK_DECIDE_API_KEY', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}})
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return {'kind': 'tool', 'tool': 'spark-decide', 'request': request,
                'exit_code': -1, 'error': 'timeout', 'elapsed_seconds': time.monotonic() - started}
    try:
        response = json.loads(stdout)
    except (ValueError, TypeError):
        response = {}
    return {'kind': 'tool', 'tool': 'spark-decide', 'request': request,
            'exit_code': process.returncode, 'response': response,
            'stdout': stdout.decode(errors='replace'), 'stderr_present': bool(stderr),
            'elapsed_seconds': time.monotonic() - started}


async def trial(case, arm, client, policy, guide, output):
    from app.models import ChatMessage, Role
    from app.policy.request_policy import InvocationBudget, active_budget
    system = PROTOCOL + ('\n\nAvailable Skill instructions:\n' + guide if arm == 'with_skill' else '')
    prompt = case['prompt']
    if 'request' in case:
        prompt += '\nRequest: ' + json.dumps(case['request'], ensure_ascii=False)
    messages = [ChatMessage(role=Role.SYSTEM, content=system), ChatMessage(role=Role.USER, content=prompt)]
    trace = []
    final = None
    error = None
    budget = InvocationBudget(policy)
    token = active_budget.set(budget)
    start = time.monotonic()
    try:
        for step in range(2):
            remaining = 150 - (time.monotonic() - start)
            if remaining <= 0:
                error = 'trial_timeout'
                break
            try:
                response = await client.complete(messages, model='cofounder-auto', temperature=0,
                    max_tokens=512, policy=policy.model_copy(update={'timeout_seconds': min(65, remaining)}))
            except Exception as exc:
                error = type(exc).__name__
                trace.append({'kind': 'model_error', 'step': step, 'error': error})
                break
            trace.append({'kind': 'model', 'step': step, 'output': response.content,
                'usage': response.usage, 'provider': response.selected_provider,
                'model': response.selected_model, 'request_id': response.request_id,
                'fallback_used': response.fallback_used, 'finish_reason': response.finish_reason})
            if response.selected_provider not in {'local', 'qwen'} or response.fallback_used:
                error = 'provider_policy_mismatch'
                break
            try:
                value = json.loads(response.content)
            except (ValueError, TypeError):
                error = 'invalid_protocol_json'
                break
            if isinstance(value, dict) and set(value) == {'final'}:
                final = value['final']
                break
            if (not isinstance(value, dict) or set(value) != {'tool', 'request'}
                    or value.get('tool') != 'spark-decide' or step != 0
                    or any(row['kind'] == 'tool' for row in trace)):
                error = 'illegal_tool_or_step_limit'
                break
            # Candidate cannot enlarge request scope, grants, data, or destinations.
            if case.get('request') != value['request']:
                error = 'tool_arguments_not_authorized'
                break
            row = await tool_call(value['request'], max(0.1, min(65, 150 - (time.monotonic() - start))))
            trace.append(row)
            messages.extend([ChatMessage(role=Role.ASSISTANT, content=response.content),
                ChatMessage(role=Role.USER, content='Tool result: ' + json.dumps(row.get('response', {'error': row.get('error')})) + '\nFinish now using the final protocol.')])
            # Preserve the first actual model response and tool receipt before next inference.
            output.write_text(json.dumps({'status': 'in_progress', 'case_id': case['id'], 'arm': arm,
                                         'system': system, 'prompt': prompt, 'trace': trace}, ensure_ascii=False, indent=2))
    finally:
        active_budget.reset(token)
    checks = score(case, trace, final)
    if error:
        checks['passed'] = False
    result = {'status': 'finished', 'case_id': case['id'], 'arm': arm, 'system': system,
              'prompt': prompt, 'trace': trace, 'final': final, 'error': error,
              'score': checks, 'elapsed_seconds': time.monotonic() - start,
              'policy': policy.model_dump(mode='json'), 'model_tokens_reserved': budget.reserved_tokens}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


async def main(args):
    data = cases()
    if args.precheck:
        data = json.loads((HERE.parent / 't22/cases.json').read_text())
    guide = (SKILL / 'SKILL.md').read_text()
    frozen = json.loads((HERE / 'freeze.json').read_text())
    for path, key in [(Path(__file__), 'harness_sha256'), (SKILL / 'SKILL.md', 'skill_sha256'), (SKILL / 'scripts/decide.py', 'script_sha256')]:
        if digest(path.read_bytes()) != frozen[key]:
            raise ValueError('frozen_artifact_changed:' + key)
    if not args.execute:
        print(json.dumps({'status': 'dry_run', 'cases': len(data), 'trials': len(data) * 4,
                          'max_model_calls': len(data) * 8, 'max_tool_calls': len(data) * 4, 'retries': 0,
                          'official_tier3': False, 'skill': str(SKILL)}))
        return
    from app.clients.gateway import GatewayClient
    from app.request_constraints import RequestPolicy
    if not os.environ.get('SPARK_DECIDE_URL') or not os.environ.get('SPARK_DECIDE_API_KEY'):
        raise SystemExit('existing SPARK_DECIDE_URL and SPARK_DECIDE_API_KEY references are required')
    args.output.mkdir(parents=True, exist_ok=False)
    policy = RequestPolicy(privacy='public', allowed_providers=frozenset({'local'}),
        permissions=frozenset({'model:invoke'}), max_attempts=2,
        max_total_tokens=32768, timeout_seconds=150, cloud_call_budget=0)
    client = GatewayClient.from_environment()
    client.policy = policy
    config = {'harness': 'project-bounded-json-agent-t28-v2', 'official_tier3': False,
        'cases_sha256': digest((HERE / 'cases.json').read_bytes()),
        'harness_sha256': digest(Path(__file__).read_bytes()),
        'skill_sha256': digest(guide.encode()), 'script_sha256': digest((SKILL / 'scripts/decide.py').read_bytes()),
        'protocol_sha256': digest(PROTOCOL.encode()), 'scorer_version': SCORER_VERSION,
        'policy': policy.model_dump(mode='json'), 'max_steps_per_trial': 2,
        'max_tool_calls_per_trial': 1, 'retries': 0, 'concurrency': 1,
        'tools_both_arms': ['spark-decide'], 'tool_scope': 'exact frozen request only',
        'execution_sandbox': 'same host process restrictions; fixed portable client only; no candidate shell',
        'arm_difference': 'SKILL.md appended to system message only', 'endpoint_secrets': 'environment references, not stored'}
    (args.output / 'config.json').write_text(json.dumps(config, indent=2))
    results = []
    for attempt in range(1, (1 if args.precheck else 2) + 1):
        for index, case in enumerate(data):
            arms = ('without_skill', 'with_skill') if (index + attempt) % 2 == 0 else ('with_skill', 'without_skill')
            for arm in arms:
                if os.environ.get('T28_STOP_FILE') and Path(os.environ['T28_STOP_FILE']).exists():
                    raise RuntimeError('resource_or_active_job_stop')
                output = args.output / f"{case['id']}-{arm}-{attempt}.json"
                # Reserve before inference so interruption never makes this trial look absent.
                with output.open('x') as reserved:
                    json.dump({'status': 'reserved', 'case_id': case['id'], 'arm': arm,
                               'attempt': attempt}, reserved)
                result = await trial(case, arm, client, policy, guide, output)
                result['attempt'] = attempt
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
                results.append(result)
                print(json.dumps({'case':case['id'],'arm':arm,'attempt':attempt,'passed':result['score']['passed'],'error':result['error']}),flush=True)
    models = {row['model'] for result in results for row in result['trace'] if row['kind'] == 'model'}
    summary = {'official_tier3': False, 'trials': len(results), 'models': sorted(models, key=str),
        'model_identity_consistent': len(models) == 1 and None not in models,
        'passes': {arm: sum(r['score']['passed'] for r in results if r['arm'] == arm) for arm in ('without_skill','with_skill')},
        'model_calls': sum(row['kind'] in {'model','model_error'} for r in results for row in r['trace']),
        'tool_calls': sum(row['kind'] == 'tool' for r in results for row in r['trace']),
        'model_usage': [row['usage'] for r in results for row in r['trace'] if row['kind'] == 'model'],
        'decision_usage': [row.get('response', {}).get('usage') for r in results for row in r['trace'] if row['kind'] == 'tool'],
        'cost': None, 'billing_status': 'unknown', 'scope': 'development protocol check; excluded from formal scores' if args.precheck else 'frozen 10-case bounded Agent A/B; 2 attempts per arm; no generalization or official PASS claim'}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--precheck', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('work/t28-v2-pairs'))
    asyncio.run(main(parser.parse_args()))
