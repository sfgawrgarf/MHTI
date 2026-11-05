"""Exercise production CORS configuration with explicit deployment settings."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize('lan,extra,origin,allowed', [
    ('true', '', 'http://192.168.5.88:8000', True),
    ('true', '', 'https://10.2.3.4:443', True),
    ('true', '', 'http://172.31.1.2', True),
    ('true', '', 'https://untrusted.example', False),
    ('false', '', 'http://192.168.5.88:8000', False),
    ('false', 'https://ui.example', 'https://ui.example', True),
    ('true', '*', 'https://untrusted.example', False),
])
def test_credentialed_cors_is_limited_to_configured_or_opted_in_lan(lan, extra, origin, allowed):
    script = '''
import json,sys
from fastapi.testclient import TestClient
from server.main import app
response = TestClient(app).options('/health', headers={
    'Origin':sys.argv[1], 'Access-Control-Request-Method':'GET',
    'Access-Control-Request-Headers':'authorization',
})
print(json.dumps({'status':response.status_code,'origin':response.headers.get('access-control-allow-origin')}))
'''
    env = dict(os.environ, CORS_ALLOW_ALL=lan, CORS_ORIGINS=extra)
    result = subprocess.run([sys.executable, '-c', script, origin], env=env,
                            text=True, capture_output=True, check=True, timeout=30)
    response = json.loads(result.stdout.strip().splitlines()[-1])
    assert response['status'] == (200 if allowed else 400)
    assert response['origin'] == (origin if allowed else None)
