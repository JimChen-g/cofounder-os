#!/usr/bin/env python3
"""Frozen train-only collection, sequential Qwen/Step, no hidden-test access."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
SCORER = 'json-exact-types-v1'


def exact(value, expected):
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return value.keys() == expected.keys() and all(exact(value[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(value) == len(expected) and all(exact(a, b) for a, b in zip(value, expected))
    return value == expected


def score(output, expected):
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        return {'passed': False, 'reason': 'invalid_json', 'scorer_version': SCORER}
    passed = exact(value, expected)
    return {'passed': passed, 'reason': 'exact_match' if passed else 'wrong_value_or_type', 'scorer_version': SCORER}


def frozen_cases():
    manifest = json.loads((HERE / 'manifest.json').read_text())
    raw = (HERE / 'development-cases.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['development_file_sha256']:
        raise ValueError('frozen_dataset_hash_mismatch')
    cases = json.loads(raw)
    families = {}
    for row in manifest['cases']:
        families.setdefault(row['family_id'], set()).add(row['split'])
    if any(len(splits) != 1 for splits in families.values()):
        raise ValueError('family_split_leakage')
    return manifest, cases


async def collect(args):
    manifest, cases = frozen_cases()
    # Calibration is frozen but not used for any selection in T21.
    cases = [c for c in cases if c['split'] == 'train'][:args.limit]
    if not args.execute:
        print(json.dumps({'status': 'dry_run', 'cases': len(cases), 'maximum_calls': len(cases)*2,
                          'retries': 0, 'maximum_step_calls': len(cases), 'hidden_test_access': False}))
        return
    from app.clients.gateway import GatewayClient
    from app.models import ChatMessage, Role
    from app.request_constraints import RequestPolicy
    from app.policy.request_policy import InvocationBudget, active_budget
    args.output.mkdir(parents=True, exist_ok=False)
    policy = RequestPolicy(privacy='public', allowed_providers=frozenset({'local','step'}),
        permissions=frozenset({'model:invoke','cloud:invoke'}), max_attempts=2,
        max_total_tokens=8192, timeout_seconds=90, cloud_call_budget=1)
    client = GatewayClient.from_environment()
    client.policy = policy
    started = time.monotonic()
    call_count = 0
    total_tokens = 0
    results = []
    for case in cases:
        if time.monotonic() - started >= 600 or total_tokens >= 196608:
            break
        records = []
        budget = InvocationBudget(policy)
        token = active_budget.set(budget)
        try:
            for channel in ('local','step'):
                if time.monotonic() - started >= 600:
                    break
                record = {'candidate': channel, 'output': None, 'usage': {}, 'error': None,
                          'checks': {'passed': False, 'reason': 'not_run'}, 'started_at': datetime.now(timezone.utc).isoformat()}
                tick = time.monotonic()
                call_count += 1
                try:
                    response = await client.complete([ChatMessage(role=Role.SYSTEM, content='Return only the requested JSON. Do not execute code or use tools.'),
                        ChatMessage(role=Role.USER, content=case['prompt'])], temperature=0, max_tokens=512,
                        policy=policy.model_copy(update={'allowed_providers': frozenset({channel})}))
                    record.update(output=response.content, usage=response.usage,
                        selected_provider=response.selected_provider, selected_model=response.selected_model,
                        request_id=response.request_id, fallback_used=response.fallback_used)
                    if response.selected_provider not in ({'qwen','local'} if channel == 'local' else {'step'}) or response.fallback_used:
                        record['error'] = 'provider_identity_mismatch'
                    else:
                        record['checks'] = score(response.content, case['expected'])
                    used = response.usage.get('total_tokens')
                    if isinstance(used, int):
                        total_tokens += used
                except Exception as exc:
                    record['error'] = type(exc).__name__  # No transport URLs/keys in evidence.
                record['elapsed_seconds'] = time.monotonic() - tick
                records.append(record)
                # Persist each response immediately, including failures.
                (args.output / f"{case['case_id']}-{channel}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        finally:
            active_budget.reset(token)
        passing = {r['candidate'] for r in records if r['error'] is None and r['checks']['passed']}
        label = ('incomplete' if len(records) != 2 else 'both' if len(passing)==2 else next(iter(passing), 'neither'))
        result = {'case_id':case['case_id'], 'family_id':case['family_id'], 'split':case['split'],
                  'dataset_version':manifest['dataset_version'], 'source_kind':'synthetic',
                  'prompt':case['prompt'], 'oracle':case['expected'], 'scorer_version':SCORER,
                  'features_before_call':{'task_kind':case['family_id'],'input_size':len(case['prompt'].encode()),
                    'privacy':'public','allowed_providers':['local','step'],'known_tool_requirements':[]},
                  'candidates':records,'label':label,'cost':None,'billing_status':'unknown'}
        results.append(result)
        (args.output / f"{case['case_id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    summary = {'status':'completed' if len(results)==len(cases) else 'budget_stopped',
               'actual_cases':len(results),'actual_calls':call_count,'reported_total_tokens':total_tokens,
               'missing_usage_records':sum(not r['usage'] for v in results for r in v['candidates']),
               'max_calls':len(cases)*2,'retries':0,'concurrency':1,'calibration_used':False,'test_used':False,
               'manifest_sha256':hashlib.sha256((HERE/'manifest.json').read_bytes()).hexdigest()}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--limit',type=int,choices=range(1,13),default=3)
    parser.add_argument('--output',type=Path,default=Path('work/t21-collection'))
    asyncio.run(collect(parser.parse_args()))
