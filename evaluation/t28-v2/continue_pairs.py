#!/usr/bin/env python3
"""Continue only absent v2 trial IDs; never overwrite or automatically replay a started trial."""
from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import os
from pathlib import Path

import run_pairs as harness


def planned_trials(data):
    for attempt in (1, 2):
        for index, case in enumerate(data):
            arms = ('without_skill', 'with_skill') if (index + attempt) % 2 == 0 else ('with_skill', 'without_skill')
            for arm in arms:
                yield f"{case['id']}-{arm}-{attempt}.json", case, arm, attempt


def inventory(output, data):
    retained, missing = {}, []
    for name, case, arm, attempt in planned_trials(data):
        path = output / name
        if path.exists():
            content = path.read_bytes()
            record = json.loads(content)
            if (record.get('status') != 'finished' or record.get('case_id') != case['id']
                    or record.get('arm') != arm or record.get('attempt') != attempt):
                raise ValueError('existing_trial_requires_manual_inspection:' + name)
            retained[name] = hashlib.sha256(content).hexdigest()
        else:
            missing.append(name)
    return retained, missing


async def main(args):
    data = harness.cases()
    freeze = json.loads((harness.HERE / 'freeze.json').read_text())
    for path, key in [(harness.HERE / 'run_pairs.py', 'harness_sha256'),
                      (harness.SKILL / 'SKILL.md', 'skill_sha256'),
                      (harness.SKILL / 'scripts/decide.py', 'script_sha256')]:
        if harness.digest(path.read_bytes()) != freeze[key]:
            raise ValueError('frozen_artifact_changed:' + key)
    config_path = args.output / 'config.json'
    config = json.loads(config_path.read_text())
    for key in ('cases_sha256', 'harness_sha256', 'skill_sha256', 'script_sha256'):
        if config[key] != freeze[key]:
            raise ValueError('source_batch_mismatch:' + key)
    retained, missing = inventory(args.output, data)
    print(json.dumps({'retained': len(retained), 'missing': missing, 'execute': args.execute}))
    if not args.execute:
        return
    from app.clients.gateway import GatewayClient
    from app.request_constraints import RequestPolicy
    policy = RequestPolicy.model_validate(config['policy'])
    client = GatewayClient.from_environment()
    client.policy = policy
    guide = (harness.SKILL / 'SKILL.md').read_text()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    manifest = {'version': 't28-v2-continuation-1', 'created_at': stamp,
                'config_sha256': harness.digest(config_path.read_bytes()),
                'retained_sha256': retained, 'planned_missing': missing,
                'no_replay_of_existing_files': True}
    # A manifest precedes any calls. It records intent, never claims calls completed.
    with (args.output / f'continuation-{stamp}.json').open('x') as file:
        json.dump(manifest, file, indent=2)
    for name, case, arm, attempt in planned_trials(data):
        if name not in missing:
            continue
        if os.environ.get('T28_STOP_FILE') and Path(os.environ['T28_STOP_FILE']).exists():
            raise RuntimeError('resource_or_active_job_stop')
        path = args.output / name
        # Exclusive reservation prevents a concurrent resumer or crash from replaying this ID.
        with path.open('x') as file:
            json.dump({'status': 'reserved', 'case_id': case['id'], 'arm': arm,
                       'attempt': attempt}, file)
        result = await harness.trial(case, arm, client, policy, guide, path)
        result['attempt'] = attempt
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get('error') == 'provider_policy_mismatch':
            raise RuntimeError('provider_policy_mismatch')
    for name, expected in retained.items():
        if harness.digest((args.output / name).read_bytes()) != expected:
            raise RuntimeError('retained_trial_changed:' + name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    asyncio.run(main(parser.parse_args()))
