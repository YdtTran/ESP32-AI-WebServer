#!/usr/bin/env python3
"""Live multi-user provisioning smoke test; run only against a test deployment.

Requires PLATFORM_TEST_USER_A, PLATFORM_TEST_PASSWORD_A, PLATFORM_TEST_USER_B,
PLATFORM_TEST_PASSWORD_B, and optionally PLATFORM_TEST_BRIDGE_URL. The two
accounts must already exist. Creates and deletes one test agent and device.
"""
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

BASE = os.getenv('PLATFORM_TEST_BRIDGE_URL', 'http://localhost:8005').rstrip('/')


def request(path, method='GET', body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request_headers = {'Content-Type': 'application/json', **(headers or {})}
    req = urllib.request.Request(BASE + path, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def main():
    names = ('PLATFORM_TEST_USER_A', 'PLATFORM_TEST_PASSWORD_A', 'PLATFORM_TEST_USER_B', 'PLATFORM_TEST_PASSWORD_B')
    if any(not os.getenv(name) for name in names):
        sys.exit('Set PLATFORM_TEST_USER_A, PLATFORM_TEST_PASSWORD_A, PLATFORM_TEST_USER_B, and PLATFORM_TEST_PASSWORD_B for two test accounts.')
    if os.environ[names[0]] == os.environ[names[2]]:
        sys.exit('The two test accounts must be different.')
    users = []
    for username, password in ((os.environ[names[0]], os.environ[names[1]]), (os.environ[names[2]], os.environ[names[3]])):
        status, result = request('/api/auth/login', 'POST', {'username': username, 'password': password})
        assert status == 200, f'Login failed: HTTP {status}'
        users.append({'Authorization': 'Bearer ' + result['token']})
    alice, bob = users
    assert request('/api/agents')[0] == 401
    status, result = request('/api/agents', 'POST', {'name': 'Provisioning smoke test'}, alice)
    assert status == 200, f'Agent creation failed: HTTP {status}'
    agent_id = result['agent']['id']
    try:
        assert request('/api/agents/' + agent_id, headers=bob)[0] == 404
        assert request('/api/agents/' + agent_id, 'PUT', {'name': 'not allowed'}, bob)[0] == 404
        mac = '02:' + ':'.join(f'{value:02X}' for value in secrets.token_bytes(5))
        proof = secrets.token_hex(32)
        device_headers = {'Device-Id': mac, 'X-Enrollment-Key': proof}
        status, result = request('/xiaozhi/ota/', 'POST', {}, device_headers)
        assert status == 200 and result.get('activation'), f'OTA enrollment failed: HTTP {status}'
        code = result['activation']['code']
        bind = {'agent_id': agent_id, 'code': code, 'device_name': 'Provisioning smoke test'}
        assert request('/api/devices/activation/bind', 'POST', bind, bob)[0] == 404
        status, result = request('/api/devices/activation/bind', 'POST', bind, alice)
        assert status == 200 and 'device_token' not in result.get('device', {}), f'Binding failed: HTTP {status}'
        assert request('/xiaozhi/ota/activate', 'POST', {}, {'Device-Id': mac, 'X-Enrollment-Key': secrets.token_hex(32)})[0] != 200
        status, result = request('/xiaozhi/ota/activate', 'POST', {}, device_headers)
        assert status == 200 and result.get('device_token'), f'Device confirmation failed: HTTP {status}'
        device = {'Authorization': 'Bearer ' + result['device_token']}
        assert request('/api/devices/me', headers=device)[1]['agent_id'] == agent_id
        assert any(item['mac_address'] == mac for item in request('/api/devices', headers=alice)[1]['devices'])
        assert all(item['mac_address'] != mac for item in request('/api/devices', headers=bob)[1]['devices'])
        assert request('/api/devices/unbind', 'POST', {'mac_address': mac}, bob)[0] == 404
        assert request('/api/devices/me/unbind', 'POST', {}, device)[0] == 200
        assert request('/api/devices/me', headers=device)[0] == 401
        print('Multi-user provisioning smoke test passed.')
    finally:
        request('/api/agents/' + agent_id, 'DELETE', headers=alice)
        for user in users:
            request('/api/auth/logout', 'POST', headers=user)


if __name__ == '__main__':
    main()
