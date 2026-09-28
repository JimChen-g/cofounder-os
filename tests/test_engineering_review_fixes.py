"""Adversarial independent-review regressions; synthetic, never live run evidence."""
import json
import subprocess
from pathlib import Path

import pytest

from app.engineering.workspace import ALLOWED, Workspace, git
from app.services.engineering_delivery import DeliveryConflict
from tests.test_engineering_delivery import env as engineering_env, feedback, ready, action
from tests.test_engineering_execution import repo as engineering_repo

env = engineering_env
repo = engineering_repo


@pytest.mark.asyncio
@pytest.mark.parametrize('exhausted', ['time', 'short_window', 'calls', 'tokens', 'task'])
async def test_feedback_preflight_preserves_entire_pending_snapshot(env, exhausted):
    p, s, c = env
    rid = await ready(env)
    key = p.get_run(rid).run.metadata['engineering_budget_id']
    if exhausted == 'task':
        with p.orchestration.repository.transaction(rid) as tx:
            task = tx.list_tasks()[0]
            task.attempt_count = task.max_attempts
            tx.save_task(task)
    else:
        with s.budgets.connect() as db:
            statement = {'time': 'started=started-660', 'short_window': 'started=started-599.999', 'calls': 'attempts=3',
                         'tokens': 'tokens=99999'}[exhausted]
            db.execute('UPDATE budgets SET ' + statement + ' WHERE id=?', (key,))
    before = p.get_run(rid).model_dump(mode='json')
    with pytest.raises(DeliveryConflict, match='repair_(budget|attempts)_exhausted'):
        c.act(rid, 'founder', 'feedback', feedback(p, rid))
    assert p.get_run(rid).model_dump(mode='json') == before
    assert s.gateway.implementations == 1
    c.act(rid, 'founder', 'approve', action(p, rid))
    assert p.get_run(rid).run.status == 'completed'


@pytest.mark.asyncio
async def test_corrupt_unrelated_history_cannot_block_new_run(env):
    p, s, c = env
    old = s.root / 'unrelated-evidence'
    old.mkdir()
    (old / 'result.json').write_text('{truncated')
    await ready(env)
    assert (old / 'result.json').read_text() == '{truncated'


@pytest.mark.asyncio
async def test_repair_failure_has_consistent_delivery_terminal_state(env):
    p, s, c = env
    rid = await ready(env)
    c.act(rid, 'founder', 'feedback', feedback(p, rid))
    async def fail(*args, **kwargs):
        raise RuntimeError('synthetic failure')
    s.gateway.complete = fail
    assert (await s.execute(rid)).status == 'failed'
    assert p.get_run(rid).run.metadata['delivery']['state'] == 'repair_failed'
    assert p.get_run(rid).run.metadata['delivery']['history']


def test_candidate_survives_bundle_restore(repo, tmp_path):
    workspace = Workspace(repo, tmp_path / 'tasks', 'HEAD')
    workspace.apply_files({p: '# synthetic\n' for p in ALLOWED})
    commit = workspace.snapshot_commit()
    refs = git(repo, 'for-each-ref', '--contains', commit, '--format=%(refname)')
    assert 'refs/cofounder/candidates/' in refs
    bundle = tmp_path / 'all.bundle'
    git(repo, 'bundle', 'create', str(bundle), '--all')
    restored = tmp_path / 'restored.git'
    subprocess.run(['git', 'clone', '--mirror', str(bundle), str(restored)], check=True, capture_output=True)
    assert git(restored, 'show', commit + ':' + ALLOWED[0]) == '# synthetic\n'


def test_atomic_save_keeps_prior_evidence_on_replace_failure(repo, tmp_path, monkeypatch):
    import app.engineering.workspace as module
    workspace = Workspace(repo, tmp_path / 'tasks', 'HEAD')
    workspace.save('result.json', {'complete': True})
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(module.os, 'replace', fail)
    with pytest.raises(OSError):
        workspace.save('result.json', {'complete': False})
    assert json.loads((workspace.evidence / 'result.json').read_text()) == {'complete': True}


def test_import_time_deepcopy_patch_cannot_hide_input_mutation(tmp_path):
    from app.engineering import trusted_runner
    import sys
    candidate = tmp_path / 'materials.py'
    candidate.write_text('import copy\ncopy.deepcopy = lambda value: value\ndef check_material_completeness(payload):\n    payload["materials"].clear()\n    return {}\n')
    script = Path(trusted_runner.__file__).read_text().replace('/candidate/app/insurance_poc/materials.py', str(candidate))
    runner = tmp_path / 'runner.py'
    runner.write_text(script)
    result = subprocess.run([sys.executable, str(runner)], input=json.dumps([{'materials': [1]}]),
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)[0]['unchanged'] is False


@pytest.mark.parametrize('quote', ['# unrelated', 'import os', 'from os import path'])
def test_non_substantive_review_quotes_rejected(quote):
    from app.engineering.service import Review, validate_review_evidence
    from tests.engineering_review_helpers import REVIEW_KEYS
    review = Review.model_validate({'patch_sha': 'a', 'conclusion': 'passed', 'findings': [],
        'checks': {key: {'path': ALLOWED[1] if key == 'tests' else ALLOWED[0],
                        'line': 1, 'evidence': quote, 'satisfied': True} for key in REVIEW_KEYS}})
    with pytest.raises(RuntimeError, match='not_substantive'):
        validate_review_evidence(review, {path: quote for path in ALLOWED})


def test_spoofed_pytest_summary_does_not_detect_mutant():
    import sys
    from app.engineering.trusted_runner import bounded_process
    from app.engineering.workspace import mutation_detected
    report = bounded_process([sys.executable, '-c',
        'import os; os.write(1, b"42 passed in 0.01s\\n"); os._exit(0)'],
        timeout=2, stop=lambda: None, cancelled=lambda: False)
    assert report['exit_code'] == 0
    assert '42 passed' in report['log']
    assert not mutation_detected(report)


@pytest.mark.parametrize('code,summary,expected', [
    (1, '1 failed in 0.01s', True), (0, '42 passed in 0.01s', False),
    (2, '1 error in 0.01s', False), (1, '1 failed, 1 error in 0.01s', False),
])
def test_mutation_requires_actual_test_failure(code, summary, expected):
    from app.engineering.workspace import mutation_detected
    assert mutation_detected({'exit_code': code, 'log': summary}) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_claim', [False, True])
async def test_running_engineering_recovery_never_replays_model(env, missing_claim):
    p, s, c = env
    snapshot = s.create('founder', 'recovery-no-replay')
    rid, task = snapshot.run.id, snapshot.tasks[0]
    p.orchestration.start_run(rid, actor='workflow-controller', reason='synthetic setup')
    p.orchestration.mark_task_ready(rid, task.id, actor='workflow-controller', reason='synthetic setup')
    p.workflow_controller.agent_execution.claim_task(rid, task.id, agent_id='engineering-agent')
    if missing_claim:
        with p.orchestration.repository.transaction(rid) as tx:
            current_task = tx.get_task(task.id)
            current_task.claim_token = None
            current_task.claimed_by = None
            tx.save_task(current_task)
    result = await s.execute(rid)
    assert result.status == 'failed'
    current = p.get_run(rid)
    assert current.tasks[0].status == 'failed'
    assert current.tasks[0].attempt_count == 1
    assert current.run.metadata['termination_reason'] == 'engineering_recovery_not_supported'
    assert s.gateway.implementations == s.gateway.reviews == 0


@pytest.mark.asyncio
async def test_corrupt_current_evidence_retained_and_audited_once(env):
    p, s, c = env
    rid = await ready(env)
    workspace_id = p.get_run(rid).run.metadata['engineering_workspaces'][0]
    path = s.root / (workspace_id + '-evidence') / 'result.json'
    path.write_text('{truncated')
    assert s._run_evidence(rid) == []
    assert s._run_evidence(rid) == []
    assert path.read_text() == '{truncated'
    snapshot = p.get_run(rid)
    assert snapshot.run.metadata['quarantined_engineering_evidence'] == [workspace_id]
    assert sum(event.event_type == 'engineering.evidence_quarantined' for event in snapshot.events) == 1


@pytest.mark.asyncio
async def test_repair_intent_is_durable_before_task_write(env, monkeypatch):
    from app.state.repository import RunTransaction
    p, s, c = env
    rid = await ready(env)
    def crash(self, task):
        raise OSError('synthetic task write interruption')
    monkeypatch.setattr(RunTransaction, 'save_task', crash)
    with pytest.raises(OSError, match='synthetic'):
        c.act(rid, 'founder', 'feedback', feedback(p, rid))
    current = p.get_run(rid)
    assert current.run.metadata['delivery']['state'] == 'repair_queued'
    assert current.run.status == 'running'
    assert current.tasks[0].status == 'completed'
    assert s.gateway.implementations == 1
