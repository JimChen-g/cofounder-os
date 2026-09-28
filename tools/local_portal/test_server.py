"""Offline boundary tests: real HTTP handler, in-memory sockets, fake Spark only.

Run with: python3 -B -m unittest discover -s tools/local_portal -p test_server.py -v
No port is bound, credentials are read, or remote service is contacted.
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit


SPEC = importlib.util.spec_from_file_location("spark_ui_under_test", Path(__file__).with_name("server.py"))
portal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portal)

RID = "11111111-2222-4333-8444-555555555555"
OTHER = "99999999-2222-4333-8444-555555555555"
JOB = "77777777-2222-4333-8444-555555555555"
BASE_SHA = "a" * 40
PATCH_SHA = "b" * 64
VERSION = {"request_id": "offline-action-1", "revision": 2, "base_sha": BASE_SHA,
           "patch_sha": PATCH_SHA, "approval_id": "offline-approval-2"}


def run_record(rid=RID, owner="founder"):
    return {"id": rid, "owner": owner, "status": "waiting_approval", "metadata": {
        "engineering": True, "base_sha": BASE_SHA,
        "delivery": {k: v for k, v in VERSION.items() if k != "request_id"}}}


def evaluation_row(rid, owner, *, completed=1, tasks=2, score=40, provider="qwen"):
    return {"run_id": rid, "owner": owner, "objective": "visible-" + owner,
            "status": "completed" if completed == tasks else "failed",
            "overall_score": score, "grade": "B", "completed_tasks": completed,
            "task_count": tasks, "verified_artifact_count": completed,
            "artifact_count": tasks, "retry_count": 1, "providers": [provider],
            "agent_performance": [{"agent_id": "engineering-" + owner, "tasks": tasks,
                "completed": completed, "failed": tasks - completed, "retries": 1,
                "average_attempts": 1.5}]}


class FakeSpark:
    """A bounded fake that records every dispatch and rejects unexpected routes."""
    def __init__(self):
        self.calls = []
        self.runs = {RID: run_record(), OTHER: run_record(OTHER, "other-owner"),
                     portal.PROTECTED: run_record(portal.PROTECTED)}
        self.write_error = None
        self.invalidations = 0
        self.report = {"generated_at": "2026-09-28T00:00:00Z", "recent_runs": [
            evaluation_row(RID, "founder"),
            evaluation_row(OTHER, "other-owner", completed=99, tasks=99,
                           score=100, provider="foreign-provider")]}

    def request(self, method, target, body=b""):
        self.calls.append((method, target, body))
        path = urlsplit(target).path
        if method == "POST":
            if self.write_error:
                raise self.write_error
            if path == "/api/insurance-poc/run-jobs":
                return self.reply(202, {"job_id": JOB})
            return self.reply(200, {"dispatched": True})
        if path == "/api/health":
            return self.reply(200, {"status": "healthy"})
        if path == "/api/evaluation/summary":
            return self.reply(200, self.report)
        if path == "/api/insurance-poc/run-jobs/" + JOB:
            return self.reply(200, {"job_id": JOB, "status": "running"})
        if path.startswith("/api/runs/"):
            rid = path.split("/")[3]
            if rid in self.runs:
                return self.reply(200, {"run": self.runs[rid]})
            return self.reply(404, {"error": "not_found"})
        if path.startswith("/api/engineering/runs/"):
            rid = path.split("/")[4]
            if rid in self.runs:
                return self.reply(200, {"snapshot": {"run": self.runs[rid]}})
            return self.reply(404, {"error": "not_found"})
        if path == "/ui/assets/app.js":
            return 200, "application/javascript", (portal.BASE / "app/ui/static/app.js").read_bytes()
        raise AssertionError("Unexpected fake request: " + method + " " + target)

    def invalidate_inventory(self):
        self.invalidations += 1

    def inventory(self, owner):
        return {'runs': [{'id': rid, 'owner': run['owner'], 'status': run['status'],
                         'engineering': False} for rid, run in self.runs.items()]}

    @staticmethod
    def reply(status, body):
        return status, "application/json", json.dumps(body).encode()

    @property
    def writes(self):
        return [call for call in self.calls if call[0] == "POST"]


class FakePortal:
    # Exercise the actual owner check without starting a socket server.
    run = portal.Portal.run

    def __init__(self, spark):
        self.spark = spark
        self.origin = "http://127.0.0.1:19099"
        self.session = "offline-session-token"
        self.owner = "founder"
        self.write_lock = threading.Lock()
        self.jobs = set()


class MemorySocket:
    """StreamRequestHandler-compatible transport; no network socket exists."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, mode, buffering=-1):
        if mode != "rb":
            raise AssertionError("Unexpected transport mode")
        return self.input

    def sendall(self, data):
        self.output.extend(data)


class PermittedTests(unittest.TestCase):
    def test_intended_routes_and_bounded_queries(self):
        cases = [("GET", "/ui?view=evaluation"), ("GET", "/ui/assets/ink-paper.css"), ("GET", "/"), ("GET", "/ui?run=" + RID),
                 ("GET", "/ui/assets/app.js?v=d15-live-proof-2"),
                 ("GET", "/api/runs/" + RID + "/events?limit=200"),
                 ("GET", "/api/runs/" + RID + "/artifacts?include_content=false"),
                 ("GET", "/api/evaluation/summary?limit=200"),
                 ("POST", "/api/engineering/runs"),
                 ("POST", "/api/engineering/runs/" + RID + "/feedback")]
        for method, target in cases:
            with self.subTest(method=method, target=target):
                self.assertTrue(portal.permitted(method, target))

    def test_traversal_foreign_urls_unknown_methods_and_unbounded_queries(self):
        cases = [("GET", "https://example.invalid/api/health"),
                 ("GET", "//example.invalid/api/health"),
                 ("GET", "/ui/../api/health"), ("GET", "/ui/%2e%2e/api/health"),
                 ("GET", "/ui\\assets\\app.js"), ("GET", "/api/health#anything"),
                 ("GET", "/ui?view=unknown"), ("GET", "/?view=evaluation"), ("GET", "/ui?view=evaluation&view=evaluation"),
                 ("GET", "/api/evaluation/summary?limit=201"),
                 ("GET", "/api/evaluation/summary?limit=1&limit=2"),
                 ("GET", "/api/runs/" + RID + "/events?limit=-1"),
                 ("GET", "/api/runs/" + RID + "/events?limit=1&secret=x"),
                 ("GET", "/api/runs/" + RID + "/artifacts?include_content=1"),
                 ("POST", "/api/engineering/runs?anything=1"),
                 ("DELETE", "/api/runs/" + RID), ("GET", "/api/bridge/receipt"),
                 ("POST", "/v1/chat/completions")]
        for method, target in cases:
            with self.subTest(method=method, target=target):
                self.assertFalse(portal.permitted(method, target))


class OwnerSummaryTests(unittest.TestCase):
    def test_foreign_rows_and_aggregates_are_excluded(self):
        report = FakeSpark().report
        original = copy.deepcopy(report)
        result = portal.owner_summary(report, "founder")
        self.assertEqual(result["run_count"], 1)
        self.assertEqual(result["average_score"], 40)
        self.assertEqual(result["task_success_rate"], 50)
        self.assertEqual(result["artifact_integrity_rate"], 50)
        self.assertEqual(result["provider_distribution"], {"qwen": 1})
        self.assertEqual(result["agent_performance"][0]["tasks"], 2)
        self.assertNotIn("other-owner", json.dumps(result))
        self.assertNotIn("foreign-provider", json.dumps(result))
        self.assertEqual(report, original)

    def test_empty_owner_view_has_no_foreign_aggregates_or_division_error(self):
        result = portal.owner_summary(FakeSpark().report, "absent-owner")
        self.assertEqual(result["run_count"], 0)
        self.assertEqual(result["task_success_rate"], 0)
        self.assertEqual(result["recent_runs"], [])
        self.assertEqual(result["provider_distribution"], {})
        self.assertEqual(result["agent_performance"], [])


class HTTPGuardTests(unittest.TestCase):
    def setUp(self):
        self.spark = FakeSpark()
        self.server = FakePortal(self.spark)

    def request(self, method, path, body=None, *, cookie=True, origin=True,
                host="127.0.0.1:19099", extra_headers=None):
        raw = b"" if body is None else json.dumps(body).encode()
        headers = {"Host": host, "Content-Length": str(len(raw)),
                   "Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = "spark_ui=" + (self.server.session if cookie is True else cookie)
        if origin:
            headers["Origin"] = self.server.origin if origin is True else origin
        headers.update(extra_headers or {})
        wire = (method + " " + path + " HTTP/1.0\r\n" +
                "".join(k + ": " + v + "\r\n" for k, v in headers.items()) + "\r\n").encode() + raw
        transport = MemorySocket(wire)
        portal.Handler(transport, ("127.0.0.1", 12345), self.server)
        head, payload = bytes(transport.output).split(b"\r\n\r\n", 1)
        status = int(head.split(b" ", 2)[1])
        return status, head.decode(), payload

    def assert_blocked(self, expected, *args, **kwargs):
        status, _, _ = self.request(*args, **kwargs)
        self.assertEqual(status, expected)
        self.assertEqual(self.spark.writes, [])

    def test_missing_and_incorrect_session_block_before_spark(self):
        for cookie in (False, "wrong-session"):
            with self.subTest(cookie=cookie):
                self.assert_blocked(401, "GET", "/api/health", cookie=cookie)
        self.assertEqual(self.spark.calls, [])

    def test_write_requires_origin_even_with_valid_session(self):
        self.assert_blocked(403, "POST", "/api/engineering/runs", {"request_id": "new"}, origin=False)
        self.assertEqual(self.spark.calls, [])

    def test_foreign_origin_host_and_site_are_rejected_before_spark(self):
        for overrides in ({"origin": "https://example.invalid"},
                          {"host": "example.invalid:19099"},
                          {"extra_headers": {"Sec-Fetch-Site": "cross-site"}}):
            with self.subTest(overrides=overrides):
                self.assert_blocked(403, "GET", "/api/health", **overrides)
        self.assertEqual(self.spark.calls, [])

    def test_landing_issues_http_only_strict_session_without_remote_mutation(self):
        status, headers, _ = self.request("GET", "/", cookie=False, origin=False)
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly; SameSite=Strict; Path=/", headers)
        self.assertEqual(self.spark.calls, [])

    def test_protected_t27_cannot_be_changed(self):
        for action in ("approve", "reject", "feedback", "cancel", "interrupt"):
            with self.subTest(action=action):
                self.assert_blocked(409, "POST", "/api/engineering/runs/" + portal.PROTECTED + "/" + action, VERSION)

    def test_foreign_run_read_and_write_cannot_cross_owner_boundary(self):
        self.assert_blocked(404, "GET", "/api/engineering/runs/" + OTHER + "/candidate")
        self.assert_blocked(404, "POST", "/api/engineering/runs/" + OTHER + "/approve", VERSION)
        self.assertFalse(any("/candidate" in c[1] for c in self.spark.calls))

    def test_stale_revision_patch_base_or_approval_prevents_dispatch(self):
        for key, value in {"revision": 1, "patch_sha": "c" * 64,
                           "base_sha": "d" * 40, "approval_id": "old"}.items():
            with self.subTest(field=key):
                self.assert_blocked(409, "POST", "/api/engineering/runs/" + RID + "/approve", VERSION | {key: value})

    def test_owner_forgery_and_busy_write_are_rejected(self):
        self.assert_blocked(403, "POST", "/api/runs", {"objective": "test", "owner": "other-owner"})
        self.server.write_lock.acquire()
        try:
            self.assert_blocked(409, "POST", "/api/engineering/runs", {"request_id": "new"})
        finally:
            self.server.write_lock.release()
        self.assertEqual(self.spark.calls, [])

    def test_valid_versioned_write_is_dispatched_exactly_once(self):
        path = "/api/engineering/runs/" + RID + "/approve"
        status, _, data = self.request("POST", path, VERSION)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["dispatched"])
        self.assertEqual(len(self.spark.writes), 1)
        self.assertEqual(self.spark.writes[0][:2], ("POST", path))
        self.assertEqual(json.loads(self.spark.writes[0][2]), VERSION)
        self.assertFalse(self.server.write_lock.locked())
        self.assertEqual(self.spark.invalidations, 1)

    def test_unknown_post_result_is_not_retried_and_releases_lock(self):
        self.spark.write_error = TimeoutError("offline ambiguous write")
        status, _, _ = self.request("POST", "/api/engineering/runs/" + RID + "/approve", VERSION)
        self.assertEqual(status, 502)
        self.assertEqual(len(self.spark.writes), 1)
        self.assertFalse(self.server.write_lock.locked())
        self.assertEqual(self.spark.invalidations, 1)

    def test_only_created_insurance_jobs_can_be_polled(self):
        path = "/api/insurance-poc/run-jobs/" + JOB
        self.assert_blocked(404, "GET", path)
        self.assertEqual(self.spark.calls, [])
        status, _, _ = self.request("POST", "/api/insurance-poc/run-jobs", {"mission": "fixture", "attachments": []})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(self.spark.writes[-1][2])["owner"], "founder")
        self.assertEqual(self.request("GET", path)[0], 200)

    def test_evaluation_route_filters_foreign_aggregate_data(self):
        status, _, data = self.request("GET", "/api/evaluation/summary?limit=50")
        self.assertEqual(status, 200)
        self.assertNotIn(b"other-owner", data)
        self.assertNotIn(b"foreign-provider", data)
        self.assertEqual(json.loads(data)["run_count"], 1)

    def test_decision_assets_are_exact_repository_source_without_remote_fetch(self):
        status, _, data = self.request("GET", "/ui/assets/app.js?v=d15-live-proof-2")
        self.assertEqual(status, 200)
        self.assertEqual(data, (portal.UI / 'app.js').read_bytes())
        self.assertEqual(self.spark.calls, [])

    def test_decision_document_is_exact_repository_source(self):
        status, headers, data = self.request("GET", "/ui", cookie=False, origin=False)
        self.assertEqual(status, 200)
        self.assertEqual(data, (portal.UI / 'index.html').read_bytes())
        self.assertIn('HttpOnly; SameSite=Strict', headers)
        self.assertEqual(self.spark.calls, [])

    def test_engineering_evaluation_has_facts_but_no_workflow_score(self):
        inventory = {'runs': [{'id': RID, 'owner': 'founder', 'status': 'waiting_approval',
            'engineering': True, 'delivery_state': 'pending', 'expires_at': '2020-01-01T00:00:00Z',
            'revision': 3, 'termination_reason': None}]}
        result = portal.owner_summary(self.spark.report, 'founder', inventory)
        self.assertEqual(result['run_count'], 0)
        self.assertEqual(result['engineering_run_count'], 1)
        row = result['recent_runs'][0]
        self.assertIsNone(row['overall_score'])
        self.assertEqual(row['grade'], 'not_applicable')
        self.assertEqual(row['expires_at'], '2020-01-01T00:00:00Z')
        self.assertEqual(row['status'], 'waiting_approval')


class InventoryCacheTests(unittest.TestCase):
    def test_status_and_list_share_read_and_writes_invalidate(self):
        spark = portal.Spark(Path('unused'), Path('unused'), 'unused', 22, 'unused', token_file=Path('unused'))
        calls = []
        spark._inventory = lambda owner: calls.append(owner) or {'runs': [], 'owner': owner}
        self.assertEqual(spark.inventory('a'), spark.inventory('a'))
        self.assertEqual(calls, ['a'])
        spark.inventory('b')
        self.assertEqual(calls, ['a', 'b'])
        spark.invalidate_inventory()
        spark.inventory('a')
        self.assertEqual(calls, ['a', 'b', 'a'])


if __name__ == "__main__":
    unittest.main()
