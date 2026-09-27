import subprocess

import pytest

from app.engineering.workspace import ALLOWED, Workspace, git
from app.engineering.service import Review


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', str(root)], check=True, capture_output=True)
    for folder in ('app/insurance_poc', 'tests'):
        (root / folder).mkdir(parents=True)
        (root / folder / '.keep').touch()
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'base')
    return root


def test_workspace_scope_retention_and_cleanup(repo, tmp_path):
    workspace = Workspace(repo, tmp_path / 'tasks', 'HEAD')
    workspace.apply_files({path: '# fixture only\n' for path in ALLOWED})
    digest = workspace.verify()
    assert len(digest) == 64
    commit = workspace.snapshot_commit()
    assert len(commit) == 40
    assert git(repo, "rev-parse", commit + "^").strip() == workspace.base
    assert git(repo, "show", commit + ":" + ALLOWED[0]) == "# fixture only\n"
    assert workspace.verify() == digest
    assert not (repo / ALLOWED[0]).exists()
    with pytest.raises(ValueError, match='active'):
        workspace.cleanup()
    workspace.close('failed')
    workspace.cleanup()
    assert not workspace.path.exists()
    assert (workspace.evidence / 'patch.diff').exists()


@pytest.mark.parametrize('path', ['../secret', '/tmp/secret', '.git/config', 'other.py'])
def test_bad_path_rejected(repo, tmp_path, path):
    workspace = Workspace(repo, tmp_path / 'tasks', 'HEAD')
    with pytest.raises(ValueError, match='paths'):
        workspace.apply_files({path: 'bad'})
    assert git(repo, 'status', '--porcelain') == ''


def test_symlink_rejected(repo, tmp_path):
    workspace = Workspace(repo, tmp_path / 'tasks', 'HEAD')
    (workspace.path / ALLOWED[0]).symlink_to(tmp_path / 'secret')
    with pytest.raises(ValueError):
        workspace.apply_files({p: 'bad' for p in ALLOWED})
    assert not (tmp_path / 'secret').exists()


def test_outside_change_rejected(repo, tmp_path):
    workspace = Workspace(repo, tmp_path / 'tasks', 'HEAD')
    (workspace.path / 'outside.py').write_text('bad')
    with pytest.raises(ValueError, match='scope'):
        workspace.verify()


def test_shell_command_rejected(repo, tmp_path):
    workspace = Workspace(repo, tmp_path / 'tasks', 'HEAD')
    with pytest.raises(ValueError, match='allowlisted'):
        workspace.test(['sh', '-c', 'echo unsafe'], 'ignored')


def test_review_requires_evidence_and_severity():
    with pytest.raises(ValueError):
        Review.model_validate({'patch_sha': 'a', 'conclusion': 'passed',
                              'findings': [{'path': 'x', 'line': 1}]})


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,expected', [('blocking','failed'), ('stale','failed'), ('malformed','failed'), ('passed','completed')])
async def test_controller_review_gate_binds_patch(repo, tmp_path, monkeypatch, mode, expected):
    """Synthetic governance unit test; never represented as live Agent evidence."""
    import json
    from app.clients.gateway import GatewayClient, GatewayCompletion
    from app.config import Settings
    from app.engineering.service import EngineeringService
    from app.services.product_api import build_product_api_service

    class SyntheticGateway(GatewayClient):
        async def complete(self, messages, **kwargs):
            system = messages[0].content
            if 'implementation Agent' in system:
                content = json.dumps({'implementation':'# synthetic fixture\n', 'tests':'# fixture\n'})
            else:
                payload = json.loads(messages[1].content)
                content = json.dumps({'patch_sha': 'stale' if mode == 'stale' else payload['patch_sha'],
                    'conclusion': 'passed', 'findings': [] if mode != 'blocking' else [{
                    'path':ALLOWED[0], 'line':1, 'trigger':'synthetic invalid behavior',
                    'impact':'wrong result', 'evidence':'injected unit-test finding', 'severity':'blocking'}]})
            if mode == 'malformed' and 'implementation Agent' not in system:
                content = '{invalid review'
            return GatewayCompletion(content=content,requested_model='synthetic')

    product = build_product_api_service(Settings(PRODUCT_DATA_DIR=str(tmp_path/'data')))
    service = EngineeringService(product, repo, tmp_path/'tasks')
    service.gateway = SyntheticGateway('http://invalid')
    monkeypatch.setattr(Workspace, 'test', lambda self, argv, image: {
        'exit_code':0,'timed_out':False,'patch_sha':self.verify(),'log':'synthetic gate fixture',
        'argv':argv,'cwd':'synthetic-sandbox','duration_seconds':0.001})
    snapshot = service.create('founder','unit-'+mode)
    result = await service.execute(snapshot.run.id)
    assert result.status == expected
    outputs = [a for a in result.snapshot.artifacts if a.name == 'engineering-result']
    assert bool(outputs) == (mode == 'passed')
    assert all(t.attempt_count <= 2 for t in result.snapshot.tasks)
    from app.engineering.envelopes import validate_envelope
    evidence_files = list((tmp_path/'tasks').glob('*-evidence/*-envelope.json'))
    assert evidence_files
    execution_attempts = set()
    for path in evidence_files:
        envelope = json.loads(path.read_text())
        validate_envelope(envelope)
        assert len(envelope['artifact_version']['patch_sha']) == 40
        if envelope['kind'] == 'execution_result':
            execution_attempts.add(envelope['payload']['attempt'])
            assert envelope['payload']['status'] == ('succeeded' if mode == 'passed' else 'failed')
    assert execution_attempts == ({1} if mode == 'passed' else {1, 2})


def test_wrapper_normalization_never_changes_source_strings():
    import json
    from app.engineering.service import normalize_envelope
    source = 'x = ",}"; y = ",]"; z = "quoted\\\"",'
    raw = json.dumps({'implementation':source,'tests':'# test'})
    envelope = '```json\n' + raw[:-1] + ',}\n```'
    normalized, changes = normalize_envelope(envelope)
    assert json.loads(normalized)['implementation'] == source
    assert changes == ['json_fence_removed','trailing_comma_removed']
