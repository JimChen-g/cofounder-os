#!/usr/bin/env python3
"""Project-independent client wiring demonstration; synthetic server, no model call."""
import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


class Fixture(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.headers.get("Authorization") == "Bearer fixture-only"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert request["candidates"] == ["human"]
        body = json.dumps({"decision_id":"fixture-only", "refusal_reason":None, "model_version":"fixture-no-model", "policy_version":"fixture-only", "latency_ms":0, "evidence_ids":[], "request_sha256":"0"*64, "action": "human", "legal_candidates": ["human"],
                           "scores": None, "score_kind": "unavailable",
                           "shadow": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    with http.server.HTTPServer(("127.0.0.1", 0), Fixture) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="spark-decide-outside-project-") as directory:
                path = Path(directory) / "request.json"
                path.write_text(json.dumps({"task": "Need human judgment", "candidates": ["human"]}))
                env = {"SPARK_DECIDE_API_KEY": "fixture-only",
                       "SPARK_DECIDE_URL": f"http://127.0.0.1:{server.server_port}/v1/spark-decide"}
                return subprocess.run([sys.executable, str(Path(__file__).with_name("decide.py")),
                                       str(path)], cwd=directory, env=env).returncode
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    sys.exit(main())
