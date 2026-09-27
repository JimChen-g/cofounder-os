import json
import sys

from app.engineering.trusted_runner import bounded_process, compare_observations


def test_real_early_zero_exit_cannot_pass_oracle():
    result = bounded_process([sys.executable, '-c', 'import os; os._exit(0)'],
                             timeout=2, stop=lambda: None, cancelled=lambda: False)
    assert result['exit_code'] == 0
    assert not compare_observations(result['log'], [{'case_id':'valid', 'expected': {'a':1}}])['passed']


def test_real_output_flood_stopped_at_limit():
    stopped = []
    result = bounded_process([sys.executable, '-c', 'import os\nwhile True: os.write(1, b"x" * 4096)'],
                             timeout=2, stop=lambda: stopped.append(True),
                             cancelled=lambda: False, limit=12000)
    assert result['termination_reason'] == 'output_limit'
    assert result['exit_code'] != 0
    assert len(result['log']) == 12000
    assert stopped


def test_oracle_rejects_wrong_semantics_and_mutation():
    cases = [{'case_id':'one', 'expected': {'complete':False}},
             {'case_id':'bad', 'expected': {'raises':'ValueError'}}]
    reports = [{'result': {'complete':False}, 'raises':None, 'unchanged':True},
               {'result': None, 'raises':'ValueError', 'unchanged':True}]
    assert compare_observations(json.dumps(reports), cases)['passed']
    reports[0]['unchanged'] = False
    assert not compare_observations(json.dumps(reports), cases)['passed']
    reports[0]['unchanged'] = True
    reports[0]['result'] = {'complete':True}
    assert not compare_observations(json.dumps(reports), cases)['passed']


def test_closed_stdout_still_obeys_timeout():
    result = bounded_process([sys.executable, '-c', 'import os,time; os.close(1); os.close(2); time.sleep(20)'],
                             timeout=.15, stop=lambda: None, cancelled=lambda: False)
    assert result['timed_out']
    assert result['exit_code'] == 124


def test_early_exit_broken_stdin_preserves_evidence():
    result = bounded_process([sys.executable, '-c', 'import os; os._exit(0)'],
                             input_data=b'x' * 131072, timeout=2,
                             stop=lambda: None, cancelled=lambda: False)
    assert result['process_exit_code'] == 0
    assert result['log'] == ''
    assert not compare_observations(result['log'], [{'case_id':'one', 'expected':{}}])['passed']


def test_cleanup_timeout_preserves_timeout_and_partial_log():
    import subprocess
    def failed_stop():
        raise subprocess.TimeoutExpired('docker rm', 30)
    result = bounded_process([sys.executable, '-u', '-c', 'import time; print("before timeout"); time.sleep(20)'],
                             timeout=.15, stop=failed_stop, cancelled=lambda: False)
    assert result['exit_code'] == 124
    assert result['timed_out']
    assert result['termination_reason'] == 'timeout'
    assert result['cleanup_error'] == 'TimeoutExpired'
    assert 'before timeout' in result['log']
    assert result['process_exit_code'] is not None


def test_missing_process_preserves_failure_record():
    result = bounded_process(['/nonexistent/cofounder-test-executable'], timeout=1,
                             stop=lambda: None, cancelled=lambda: False)
    assert result['exit_code'] == 125
    assert result['execution_error'] == 'FileNotFoundError'
    assert result['process_exit_code'] is None


def test_cli_and_pipe_terminated_before_container_cleanup(monkeypatch):
    import subprocess
    actual_popen = subprocess.Popen
    captured = []
    def capture(*args, **kwargs):
        process = actual_popen(*args, **kwargs)
        captured.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', capture)
    cleanup_checks = []
    def stop():
        process = captured[0]
        cleanup_checks.append((process.poll() is not None, process.stdout.closed))
    result = bounded_process([sys.executable, '-c', 'import os\nwhile True: os.write(1, b"x" * 4096)'],
                             timeout=2, stop=stop, cancelled=lambda: False, limit=12000)
    assert result['termination_reason'] == 'output_limit'
    assert result['cleanup_error'] is None
    assert cleanup_checks == [(True, True)]
    assert result['process_exit_code'] is not None
