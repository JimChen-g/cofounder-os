"""Host-side oracle comparison and bounded process output; child receives inputs only."""
from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from typing import Any, Callable


def bounded_process(command: list[str], *, input_data: bytes = b'', timeout: float,
                    stop: Callable[[], None], cancelled: Callable[[], bool],
                    limit: int = 100000) -> dict[str, Any]:
    output = bytearray()
    reason = None
    cleanup_errors: list[str] = []
    execution_error = None
    process = None
    code = None
    selector = selectors.DefaultSelector()
    stopped = False

    def stop_safely() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        try:
            stop()
        except Exception as exc:
            cleanup_errors.append(type(exc).__name__)

    def terminate_local() -> None:
        nonlocal code
        if process is None:
            return
        try:
            if process.poll() is None:
                process.kill()  # Only this invocation's Docker CLI, never another service.
            # Release the attach/log pipe before asking Docker to remove the container.
            if process.stdout and not process.stdout.closed:
                try:
                    selector.unregister(process.stdout)
                except KeyError:
                    pass
                process.stdout.close()
            code = process.wait(timeout=10)
        except Exception as exc:
            cleanup_errors.append(type(exc).__name__)

    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(input_data)
            process.stdin.close()
        except BrokenPipeError:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        while selector.get_map() or process.poll() is None:
            if cancelled() or time.monotonic() >= deadline:
                reason = 'cancelled' if cancelled() else 'timeout'
                break
            for key, _ in selector.select(.05):
                chunk = os.read(key.fd, 16384)
                if not chunk:
                    selector.unregister(key.fileobj)
                    break
                if len(output) + len(chunk) > limit:
                    output.extend(chunk[:max(0, limit-len(output))])
                    reason = 'output_limit'
                    break
                output.extend(chunk)
            if reason:
                break
        if reason:
            terminate_local()
            stop_safely()
        else:
            code = process.wait(timeout=10)
    except Exception as exc:
        execution_error = type(exc).__name__
        reason = reason or 'execution_error'
    finally:
        if process is not None:
            if process.poll() is None:
                terminate_local()
                stop_safely()
            else:
                code = process.returncode
            if process.stdout and not process.stdout.closed:
                process.stdout.close()
        selector.close()
    return {'exit_code': code if reason is None and not cleanup_errors else 124 if reason == 'timeout' else 125,
            'process_exit_code': code, 'timed_out': reason == 'timeout',
            'termination_reason': reason, 'output_limit_bytes': limit,
            'execution_error': execution_error,
            'cleanup_error': ','.join(cleanup_errors) if cleanup_errors else None,
            'log': output.decode('utf-8', errors='replace')}


def compare_observations(output: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        observed = json.loads(output)
        if not isinstance(observed, list) or len(observed) != len(cases):
            raise ValueError('incomplete_report')
        checks = []
        for case, item in zip(cases, observed):
            expected = case['expected']
            good = (isinstance(item, dict) and set(item) == {'result', 'raises', 'unchanged'}
                    and item['unchanged'] is True)
            if good:
                good = (item['raises'] == 'ValueError' and item['result'] is None
                        if expected.get('raises') == 'ValueError'
                        else item['raises'] is None and item['result'] == expected)
            checks.append({'case_id': case['case_id'], 'passed': bool(good)})
        return {'passed': all(c['passed'] for c in checks), 'cases': checks}
    except (ValueError, TypeError, KeyError):
        return {'passed': False, 'error': 'invalid_or_incomplete_candidate_report'}


def child() -> None:
    # No expected answers enter this process. Host alone scores observations.
    import copy
    import importlib.util
    import sys
    inputs = json.loads(sys.stdin.read())
    spec = importlib.util.spec_from_file_location('candidate', '/candidate/app/insurance_poc/materials.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = []
    for original in inputs:
        payload = copy.deepcopy(original)
        try:
            result = module.check_material_completeness(payload)
            raises = None
        except BaseException as exc:
            result, raises = None, type(exc).__name__
        results.append({'result': result, 'raises': raises, 'unchanged': payload == original})
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    child()
