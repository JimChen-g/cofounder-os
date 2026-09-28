"""Application-owned Git workspaces; untrusted code never runs on the host."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import threading
import re
import tempfile

from .trusted_runner import bounded_process, compare_observations
from typing import Any
from uuid import uuid4

ALLOWED = ('app/insurance_poc/materials.py', 'tests/test_insurance_poc_materials.py')


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout


def mutation_detected(evidence: dict[str, Any]) -> bool:
    """Require a pytest assertion failure, not an early exit or collection error."""
    summary = evidence.get('log', '').strip().splitlines()[-1:]
    return (evidence.get('exit_code') == 1 and not evidence.get('timed_out')
            and not evidence.get('execution_error') and not evidence.get('cleanup_error')
            and bool(summary) and bool(re.search(r'\b[1-9][0-9]* failed\b', summary[0]))
            and not re.search(r'\b(errors?|skipped|xfailed|xpassed)\b', summary[0]))


class Workspace:
    def __init__(self, repo: Path, root: Path, base: str, *, seconds: int = 600) -> None:
        self.repo = repo.resolve()
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base = git(repo, 'rev-parse', '--verify', base + '^{commit}').strip()
        self.id = str(uuid4())
        self.path = self.root / self.id
        self.evidence = self.root / (self.id + '-evidence')
        self.evidence.mkdir(mode=0o700)
        self.started = time.monotonic()
        self.seconds = seconds
        self.oracle = Path(__file__).parent / "contracts/material-cases.json"
        self._cleanup_confirmed = True
        self._cancelled = threading.Event()
        self._test_finished = threading.Event()
        self._test_finished.set()
        self._test_lock = threading.Lock()
        self._state_lock = threading.Lock()
        git(repo, 'worktree', 'add', '--detach', str(self.path), self.base)
        self.save('manifest.json', {'owner': 'cofounder-engineering-v1', 'id': self.id,
                  'base_sha': self.base, 'path': str(self.path), 'allowed': ALLOWED,
                  'seconds': seconds, 'memory_mb': 512, 'cpus': 1, 'pids': 64,
                  'state': 'active'})

    def save(self, name: str, value: Any) -> None:
        target = self.evidence / name
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=self.evidence, prefix='.' + name + '.', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory = os.open(self.evidence, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def remaining(self) -> float:
        left = self.seconds - (time.monotonic() - self.started)
        if left <= 0:
            raise TimeoutError('engineering_time_budget_exhausted')
        return left

    def apply_files(self, files: dict[str, str]) -> None:
        self.remaining()
        if set(files) != set(ALLOWED):
            raise ValueError('patch_paths_not_allowed')
        for name, content in files.items():
            target = self.path / name
            if not isinstance(content, str) or len(content.encode()) > 100_000:
                raise ValueError('file_size_or_type_rejected')
            if target.exists() or target.is_symlink():
                raise ValueError('contract_requires_new_files')
            for parent in target.parents:
                if parent == self.path:
                    break
                if parent.is_symlink():
                    raise ValueError('symlink_parent_rejected')
            if not target.resolve().is_relative_to(self.path):
                raise ValueError('path_escape')
        for name, content in files.items():
            target = self.path / name
            with target.open('x') as handle:
                handle.write(content)
        self.verify()

    def verify(self) -> str:
        tracked = git(self.path, 'diff', '--name-only', self.base).splitlines()
        untracked = git(self.path, 'ls-files', '--others', '--exclude-standard').splitlines()
        if not set(tracked + untracked).issubset(ALLOWED):
            raise ValueError('out_of_scope_modification')
        for name in ALLOWED:
            p = self.path / name
            if p.is_symlink() or not p.resolve().is_relative_to(self.path):
                raise ValueError('symlink_or_escape')
        # --no-index captures additions without staging model-controlled paths.
        chunks = []
        for name in ALLOWED:
            p = self.path / name
            if p.exists():
                result = subprocess.run(['git', 'diff', '--no-index', '--', '/dev/null', name],
                                        cwd=self.path, capture_output=True, text=True, timeout=10)
                if result.returncode not in (0, 1):
                    raise RuntimeError('diff_failed')
                chunks.append(result.stdout)
        diff = ''.join(chunks)
        (self.evidence / 'patch.diff').write_text(diff)
        return hashlib.sha256(diff.encode()).hexdigest()

    def snapshot_commit(self) -> str:
        """Record the allowlisted candidate as a Git commit without hooks or checkout."""
        digest = self.verify()
        git(self.path, 'add', '--', *ALLOWED)
        tree = git(self.path, 'write-tree').strip()
        commit = git(self.path, '-c', 'user.name=Co-founder Engineering',
                     '-c', 'user.email=engineering@localhost', 'commit-tree', tree,
                     '-p', self.base, '-m', 'Candidate diff SHA256: ' + digest).strip()
        git(self.repo, 'update-ref', 'refs/cofounder/candidates/workspaces/' + self.id, commit)
        self.save('candidate-version.json', {'base_sha': self.base,
                  'patch_sha': commit, 'diff_sha256': digest})
        return commit

    def _stop_container(self) -> None:
        result = subprocess.run(['docker', 'rm', '-f', 'cofounder-test-' + self.id],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if result.returncode != 0:
            check = subprocess.run(['docker', 'ps', '-a', '--filter',
                       'name=^/cofounder-test-' + self.id + '$', '--format', '{{.Names}}'],
                       capture_output=True, text=True, check=True, timeout=10)
            if check.stdout.strip():
                raise RuntimeError('container_shutdown_unconfirmed')

    def cancel(self) -> None:
        with self._state_lock:
            self._cancelled.set()
        cleanup_error = None
        try:
            self._stop_container()
        except Exception as exc:
            cleanup_error = type(exc).__name__
        finished = self._test_finished.wait(timeout=45)
        self._cleanup_confirmed = finished and cleanup_error is None
        self.save('cancellation.json', {'test_finished': finished,
                  'cleanup_confirmed': self._cleanup_confirmed, 'cleanup_error': cleanup_error})
        if not self._cleanup_confirmed:
            raise RuntimeError('test_shutdown_unconfirmed')

    def test(self, argv: list[str], image: str, *, timeout: float = 120) -> dict[str, Any]:
        with self._test_lock:
            with self._state_lock:
                if self._cancelled.is_set():
                    raise RuntimeError('workspace_cancelled')
                self._test_finished.clear()
            try:
                return self._test(argv, image, timeout=timeout)
            finally:
                self._test_finished.set()

    def _test(self, argv: list[str], image: str, *, timeout: float) -> dict[str, Any]:
        allowed = [
            ['python', '/checks/accept_case.py', '--candidate-root', '/candidate'],
            ['python', '-m', 'pytest', '-q', '-o', 'addopts=', '-p', 'no:cacheprovider', '-p', 'pytest_asyncio.plugin',
             'tests/test_insurance_poc_materials.py'],
            ['python', '-m', 'pytest', '-q', '-o', 'addopts=', '-p', 'no:cacheprovider', '-p', 'pytest_asyncio.plugin',
             'tests/test_insurance_poc_evidence.py', 'tests/test_insurance_poc_workflow.py'],
        ]
        if argv not in allowed:
            raise ValueError('test_command_not_allowlisted')
        sha = self.verify()
        image = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                               capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        harness = Path(__file__).parent / 'trusted_runner.py'
        oracle = self.oracle
        acceptance = argv == allowed[0]
        cases = json.loads(oracle.read_text())['cases'] if acceptance else []
        actual_argv = ['python', '/trusted_runner.py'] if acceptance else argv
        command = ['docker', 'run', '--rm', '-i', '--name', 'cofounder-test-' + self.id,
                   '--network=none', '--log-driver=none',
                   '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                   '--memory=512m', '--memory-swap=512m', '--cpus=1', '--pids-limit=64',
                   '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,nosuid,size=64m',
                   *(['--tmpfs', '/candidate/app/engineering/contracts:ro,nosuid,size=4k']
                     if (self.path / 'app/engineering/contracts').exists() else []),
                   '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
                   '-e', 'PYTHONPATH=/candidate', '-v', f'{self.path}:/candidate:ro',
                   '-v', f'{harness}:/trusted_runner.py:ro', '-w', '/candidate', image, *actual_argv]
        started = time.monotonic()
        self._cleanup_confirmed = False
        self.save("active-test.json", {"state": "running", "cleanup_confirmed": False,
                  "argv": argv, "sandbox_argv": command, "patch_sha": sha})
        evidence = bounded_process(command,
                    input_data=json.dumps([c['input'] for c in cases]).encode() if acceptance else b'',
                    timeout=min(timeout, self.remaining()), stop=self._stop_container,
                    cancelled=self._cancelled.is_set)
        self._cleanup_confirmed = evidence["cleanup_error"] is None and evidence["execution_error"] is None
        if acceptance:
            gate = compare_observations(evidence['log'], cases)
        else:
            summary = evidence['log'].strip().splitlines()[-1:]
            line = summary[0] if summary else ''
            passed = re.search(r'\b([1-9][0-9]*) passed\b', line)
            gate = {'passed': bool(passed) and not re.search(r'\b(skipped|failed|error|errors|xfailed|xpassed)\b', line),
                    'summary': line}
        if argv == allowed[1] and evidence['exit_code'] == 0 and gate['passed']:
            # Versioned test-effectiveness probes. Candidate tests must reject
            # both an always-wrong return value and rejection of all valid input.
            mutations = []
            for name, source in (
                ('wrong_result', 'def check_material_completeness(payload):\n    return {}\n'),
                ('reject_valid', 'def check_material_completeness(payload):\n    raise ValueError("mutant")\n'),
            ):
                mutant = self.evidence / ('mutation-' + name + '.py')
                mutant.write_text(source)
                mutation_command = list(command)
                at = mutation_command.index('-w')
                mutation_command[at:at] = ['-v', f'{mutant}:/candidate/{ALLOWED[0]}:ro']
                observed = bounded_process(mutation_command, input_data=b'',
                    timeout=min(timeout, self.remaining()), stop=self._stop_container,
                    cancelled=self._cancelled.is_set)
                detected = mutation_detected(observed)
                mutations.append({'name': name, 'detected': detected, **observed})
                self._cleanup_confirmed = (observed['cleanup_error'] is None
                                          and observed['execution_error'] is None)
                if not self._cleanup_confirmed or observed['timed_out']:
                    break
            gate['mutation_version'] = 'generated-tests-mutations-v1'
            gate['mutations'] = mutations
            gate['passed'] = len(mutations) == 2 and all(m['detected'] for m in mutations)
        if evidence['exit_code'] == 0 and not gate['passed']:
            evidence['exit_code'] = 126
            evidence['termination_reason'] = 'invalid_test_completion'
        evidence.update({'argv': argv, 'actual_argv': actual_argv, 'sandbox_argv': command,
                    'harness': 'host-oracle-input-only-v1' if acceptance else 'pytest-summary-and-mutations-v2' if argv == allowed[1] else 'pytest-summary-v1',
                    'oracle_sha256': hashlib.sha256(oracle.read_bytes()).hexdigest() if acceptance else None,
                    'gate': gate, 'cwd': '/candidate',
                    'duration_seconds': time.monotonic()-started, 'base_sha': self.base,
                    'patch_sha': sha, 'image': image})
        evidence["cleanup_confirmed"] = self._cleanup_confirmed
        self.save("active-test.json", {"state": "finished", **evidence})
        if self.verify() != sha:
            raise ValueError('test_modified_patch')
        return evidence

    def close(self, state: str) -> None:
        manifest = json.loads((self.evidence / 'manifest.json').read_text())
        manifest['state'] = state
        self.save('manifest.json', manifest)

    def cleanup(self) -> None:
        manifest = json.loads((self.evidence / 'manifest.json').read_text())
        if (manifest['owner'] != 'cofounder-engineering-v1' or manifest['state'] not in {'failed', 'timeout', 'cancelled', 'passed_checks_pending_delivery_approval'}
                or not self._test_finished.is_set() or not self._cleanup_confirmed
                or self.path.parent != self.root or manifest['id'] != self.path.name):
            raise ValueError('workspace_not_owned_or_active')
        active_test = self.evidence / "active-test.json"
        if active_test.exists() and not json.loads(active_test.read_text()).get("cleanup_confirmed", False):
            raise ValueError("test_cleanup_unconfirmed")
        self.verify()  # retain diff and all logs before explicit retirement
        git(self.repo, 'worktree', 'remove', '--force', str(self.path))
