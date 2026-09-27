#!/usr/bin/env python3
"""Portable authenticated short-decision client; Python standard library only."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", help="JSON request path or - for stdin")
    parser.add_argument("--endpoint", default=os.environ.get("SPARK_DECIDE_URL"))
    args = parser.parse_args()
    if not args.endpoint:
        parser.error("set SPARK_DECIDE_URL or --endpoint")
    endpoint = urllib.parse.urlparse(args.endpoint)
    if endpoint.scheme not in {"http", "https"} or endpoint.username or endpoint.password:
        parser.error("endpoint must be an HTTP(S) URL without embedded credentials")
    if endpoint.scheme == "http" and endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("non-loopback endpoints require HTTPS; use an authorized local tunnel")
    token = os.environ.get("SPARK_DECIDE_TOKEN")
    if not token:
        parser.error("set SPARK_DECIDE_TOKEN")
    try:
        if args.request == "-":
            request = json.load(sys.stdin)
        else:
            with open(args.request, encoding="utf-8") as source:
                request = json.load(source)
        payload = json.dumps(request).encode()
        req = urllib.request.Request(args.endpoint, data=payload, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json"})
        # Disable redirects so credentials and private task data stay on the configured origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        with urllib.request.build_opener(NoRedirect).open(req, timeout=65) as response:
            result = json.load(response)
        if result.get("score_kind") != "unavailable" or result.get("scores") is not None:
            raise ValueError("unsupported_score_contract")
        if result.get("action") not in {"local", "step", "human", "refuse"}:
            raise ValueError("invalid_action")
        if result["action"] != "refuse" and result["action"] not in result.get("legal_candidates", []):
            raise ValueError("illegal_action")
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, urllib.error.URLError):
        print(json.dumps({"action": "refuse", "refusal_reason": "client_or_transport_failure",
                          "scores": None, "score_kind": "unavailable"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
