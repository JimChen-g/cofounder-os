"""Reproduce the smallest offline acceptance entrypoint from the checkout root."""
import subprocess
import sys

raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q',
    'tests/test_request_policy.py', 'tests/test_product_auth.py', 'tests/test_bridge.py']))
