#!/usr/bin/env python3
"""Isolated Docker verification: no models, product API, approval, or production state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from app.engineering.workspace import ALLOWED, Workspace, git


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    clone = args.root / 'repo'
    subprocess.run(['git', 'clone', '--no-hardlinks', str(args.repo), str(clone)],
                   check=True, capture_output=True)
    files = {name: git(args.repo, 'show', args.candidate + ':' + name) for name in ALLOWED}
    oracle = Path(__file__).resolve().parents[1] / 'app/engineering/contracts/material-cases-v2.json'
    generated = ['python', '-m', 'pytest', '-q', '-o', 'addopts=', '-p', 'no:cacheprovider',
                 '-p', 'pytest_asyncio.plugin', ALLOWED[1]]
    acceptance = ['python', '/checks/accept_case.py', '--candidate-root', '/candidate']
    report = {'synthetic': True, 'model_calls': 0, 'candidate': args.candidate, 'checks': []}
    for variant in ('positive', 'forged_summary', 'deepcopy_hijack'):
        workspace = Workspace(clone, args.root / 'workspaces', 'HEAD')
        workspace.oracle = oracle
        content = dict(files)
        if variant == 'forged_summary':
            content[ALLOWED[1]] = ('import os\ndef test_spoof(capsys):\n'
                '    with capsys.disabled():\n        print("42 passed in 0.01s", flush=True)\n'
                '        os._exit(0)\n')
        elif variant == 'deepcopy_hijack':
            content[ALLOWED[0]] += ('\nimport copy\ncopy.deepcopy = lambda value: value\n'
                '_original_check = check_material_completeness\n'
                'def check_material_completeness(payload):\n'
                '    result = _original_check(payload)\n'
                '    payload["materials"].clear()\n    return result\n')
        workspace.apply_files(content)
        commands = [acceptance, generated] if variant == 'positive' else [generated if variant == 'forged_summary' else acceptance]
        for command in commands:
            result = workspace.test(command, args.image)
            report['checks'].append({'variant': variant, 'argv': command,
                'exit_code': result['exit_code'], 'gate': result['gate'],
                'cleanup_confirmed': result['cleanup_confirmed'],
                'evidence_dir': str(workspace.evidence)})
            workspace.save(variant + '-' + str(len(report['checks'])) + '.json', result)
        workspace.close('passed_checks_pending_delivery_approval' if variant == 'positive' else 'failed')
    report['passed'] = all(check['cleanup_confirmed'] and
        ((check['exit_code'] == 0) if check['variant'] == 'positive' else (check['exit_code'] != 0))
        for check in report['checks'])
    (args.root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
