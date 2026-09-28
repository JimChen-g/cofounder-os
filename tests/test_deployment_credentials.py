"""Exercise deployment launcher with fake processes and disposable credentials."""
import json
import runpy
import sys
from pathlib import Path


def test_bridge_process_does_not_inherit_founder_or_provider_secrets(tmp_path, monkeypatch):
    import subprocess
    script = Path(__file__).parents[1]/'scripts/manage_t07_t10.py'
    (tmp_path/'src').mkdir()
    (tmp_path/'DEPLOYED_COMMIT').write_text('a'*40 + '\n')
    bridge = {'bridge_token': 'bridge-only', 'app_id': 'app', 'open_id': 'user'}
    product = {'product_token': 'founder-secret', 'gateway_token': 'gateway-secret',
               'step_key': 'cloud-secret', 'spark_decide_api_key': 'skill-only', 'gateway_allow_cloud': True}
    for name, value in [('bridge-config.json', bridge), ('product-config.json', product)]:
        path = tmp_path/name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    (tmp_path/'model.key').write_text('model-secret')
    monkeypatch.setenv('COFOUNDER_RUNTIME_ROOT', str(tmp_path))
    monkeypatch.setenv('PRODUCT_API_TOKEN', 'inherited-secret')
    monkeypatch.setenv('STEP_API_KEY', 'inherited-cloud')
    monkeypatch.setattr(sys, 'argv', ['manager', 'start'])
    def no_git(*args, **kwargs):
        raise AssertionError('archive deployment has no Git checkout')
    monkeypatch.setattr(subprocess, 'check_output', no_git)
    environments = []
    class Process:
        pid = 123456789
        def __init__(self, *a, **kwargs): environments.append(kwargs['env'])
    monkeypatch.setattr(subprocess, 'Popen', Process)
    runpy.run_path(str(script))
    product_env, bridge_env = environments
    assert product_env['PRODUCT_API_TOKEN'] == 'founder-secret'
    assert product_env['SPARK_DECIDE_API_KEY'] == 'skill-only'
    assert product_env['GATEWAY_ALLOW_CLOUD'] == 'true'
    assert product_env['DEPLOYMENT_COMMIT'] == 'a'*40
    assert not any('TOKEN' in key or 'KEY' in key for key in bridge_env)
    assert bridge_env['COFOUNDER_BRIDGE_CONFIG'] == str(tmp_path/'bridge-config.json')
    assert not set(product.values()) & set(bridge_env.values())
