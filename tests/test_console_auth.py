import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

bridge = importlib.import_module('platform-bridge.app')
accounts = importlib.import_module('platform-bridge.accounts')


class ConsoleAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        store = accounts.AccountStore(Path(self.directory.name) / 'test.db')
        self.devices = importlib.import_module('platform-bridge.device_db').DeviceDB(Path(self.directory.name) / 'devices.db')
        self.secret_patcher = patch.object(bridge, 'SECRET_KEY', 'test-service-secret')
        self.secret_patcher.start()
        self.addCleanup(self.secret_patcher.stop)
        self.device_patcher = patch.object(bridge, 'device_db', self.devices)
        self.device_patcher.start()
        self.addCleanup(self.device_patcher.stop)
        self.store_device_patcher = patch.object(importlib.import_module('platform-bridge.store'), 'device_db', self.devices)
        self.store_device_patcher.start()
        self.addCleanup(self.store_device_patcher.stop)
        store.create_user('alice', 'a sufficiently long password')
        self.store = store
        self.patcher = patch.object(bridge, 'accounts', store, create=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def test_login_session_and_logout(self):
        response = await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'wrong'})
        self.assertEqual(response.status_code, 401)
        response = await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})
        self.assertEqual(response.status_code, 200)
        token = response.json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        self.assertEqual((await self.client.get('/api/auth/me', headers=headers)).json()['username'], 'alice')
        self.assertEqual((await self.client.post('/api/auth/logout', headers=headers)).status_code, 200)
        self.assertEqual((await self.client.get('/api/auth/me', headers=headers)).status_code, 401)

    async def test_agents_require_login_and_are_private(self):
        self.assertEqual((await self.client.get('/api/agents')).status_code, 401)
        self.store.create_user('bob', 'another long password')
        async def login(username, password):
            response = await self.client.post('/api/auth/login', json={'username': username, 'password': password})
            return {'Authorization': 'Bearer ' + response.json()['token']}
        alice = await login('alice', 'a sufficiently long password')
        bob = await login('bob', 'another long password')
        created = await self.client.post('/api/agents', headers=alice, json={'name': 'Alice agent'})
        self.assertEqual(created.status_code, 200)
        agent_id = created.json()['agent']['id']
        self.assertEqual(len((await self.client.get('/api/agents', headers=alice)).json()['agents']), 1)
        self.assertEqual((await self.client.get('/api/agents', headers=bob)).json()['agents'], [])
        self.assertEqual((await self.client.get('/api/agents/' + agent_id, headers=bob)).status_code, 404)
        self.assertEqual((await self.client.put('/api/agents/' + agent_id, headers=bob, json={'name': 'stolen'})).status_code, 404)
        self.assertEqual((await self.client.delete('/api/agents/' + agent_id, headers=bob)).status_code, 404)
        self.assertEqual((await self.client.post('/api/agents/' + agent_id + '/chat', headers=bob, json={'message': 'hello'})).status_code, 404)

    async def test_management_routes_require_session(self):
        requests = [
            ('GET', '/api/devices', None),
            ('POST', '/api/native-dashboards/login', {'username': 'x', 'password': 'x'}),
            ('POST', '/api/devices/bind', {'agent_id': 'anything', 'mac_address': '00:11:22:33:44:55'}),
            ('POST', '/api/devices/unbind', {'mac_address': '00:11:22:33:44:55'}),
            ('POST', '/api/devices/activation', {'agent_id': 'anything'}),
            ('POST', '/api/devices/activation/bind', {'agent_id': 'anything', 'code': '123456'}),
            ('POST', '/api/devices/activation/status', {'code': '123456'}),
            ('POST', '/api/integrations/thingsboard/test', {}),
            ('GET', '/api/integrations/thingsboard/telemetry', None),
            ('POST', '/api/agents/anything/chat', {'message': 'hello'}),
        ]
        for method, path, body in requests:
            with self.subTest(path=path):
                response = await self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 401)

    async def test_device_activation_is_private_to_agent_owner(self):
        self.store.create_user('bob', 'another long password')
        async def login(username, password):
            result = await self.client.post('/api/auth/login', json={'username': username, 'password': password})
            return {'Authorization': 'Bearer ' + result.json()['token']}
        alice = await login('alice', 'a sufficiently long password')
        bob = await login('bob', 'another long password')
        result = await self.client.post('/api/agents', headers=alice, json={'name': 'Alice'})
        agent = result.json()['agent']['id']
        self.assertEqual((await self.client.post('/api/devices/activation', headers=bob, json={'agent_id': agent})).status_code, 404)
        created = await self.client.post('/api/devices/activation', headers=alice, json={'agent_id': agent})
        self.assertEqual(created.status_code, 200)
        code = created.json()['code']
        self.assertEqual((await self.client.post('/api/devices/activation/status', headers=bob, json={'code': code})).status_code, 404)
        self.assertEqual((await self.client.post('/api/devices/activation/status', headers=alice, json={'code': code})).status_code, 200)

    async def test_legacy_device_token_is_rejected(self):
        code = self.devices.create_activation_code('legacy-agent')['code']
        device = self.devices.register_device_with_code(code, '00:00:00:00:00:01')
        headers = {'Authorization': 'Bearer ' + device['device_token']}
        self.assertEqual((await self.client.get('/api/devices/me', headers=headers)).status_code, 401)
        self.assertEqual((await self.client.post('/api/devices/heartbeat', headers=headers)).status_code, 401)

    async def test_legacy_device_cannot_receive_agent_configuration(self):
        code = self.devices.create_activation_code('legacy-agent')['code']
        self.devices.register_device_with_code(code, '00:00:00:00:00:01')
        response = await self.client.post('/config/agent-models', headers={'Authorization': 'Bearer ' + bridge.SECRET_KEY}, json={'macAddress': '00:00:00:00:00:01'})
        self.assertNotEqual(response.json()['code'], 0)

    async def test_integration_rejects_another_users_agent(self):
        self.store.create_user('bob', 'another long password')
        async def login(username, password):
            result = await self.client.post('/api/auth/login', json={'username': username, 'password': password})
            return {'Authorization': 'Bearer ' + result.json()['token']}
        alice = await login('alice', 'a sufficiently long password')
        bob = await login('bob', 'another long password')
        agent = (await self.client.post('/api/agents', headers=alice, json={'name': 'Alice'})).json()['agent']['id']
        self.assertEqual((await self.client.get('/api/integrations/thingsboard/telemetry', headers=bob, params={'agent_id': agent})).status_code, 404)
        self.assertEqual((await self.client.post('/api/integrations/thingsboard/test', headers=bob, json={'agent_id': agent})).status_code, 404)

    async def test_registration_and_device_listing_are_scoped(self):
        self.store.create_user('bob', 'another long password')
        async def login(username, password):
            result = await self.client.post('/api/auth/login', json={'username': username, 'password': password})
            return {'Authorization': 'Bearer ' + result.json()['token']}
        alice = await login('alice', 'a sufficiently long password')
        bob = await login('bob', 'another long password')
        agent = (await self.client.post('/api/agents', headers=alice, json={'name': 'Alice'})).json()['agent']['id']
        code = (await self.client.post('/api/devices/activation', headers=alice, json={'agent_id': agent})).json()['code']
        registered = await self.client.post('/api/devices/register', json={'activation_code': code, 'mac_address': '00:11:22:33:44:55'})
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(len((await self.client.get('/api/devices', headers=alice)).json()['devices']), 1)
        self.assertEqual((await self.client.get('/api/agents', headers=alice)).json()['agents'][0]['deviceCount'], 1)
        self.assertEqual((await self.client.get('/api/devices', headers=bob)).json()['devices'], [])
        self.assertEqual((await self.client.post('/api/devices/unbind', headers=bob, json={'mac_address': '00:11:22:33:44:55'})).status_code, 404)

    async def test_registration_cannot_replace_another_users_device(self):
        self.store.create_user('bob', 'another long password')
        async def agent_for(username, password):
            token = (await self.client.post('/api/auth/login', json={'username': username, 'password': password})).json()['token']
            headers = {'Authorization': 'Bearer ' + token}
            agent = (await self.client.post('/api/agents', headers=headers, json={'name': username})).json()['agent']['id']
            code = (await self.client.post('/api/devices/activation', headers=headers, json={'agent_id': agent})).json()['code']
            return headers, code
        alice, code_a = await agent_for('alice', 'a sufficiently long password')
        bob, code_b = await agent_for('bob', 'another long password')
        mac = '00:11:22:33:44:55'
        self.assertEqual((await self.client.post('/api/devices/register', json={'activation_code': code_a, 'mac_address': mac})).status_code, 200)
        self.assertEqual((await self.client.post('/api/devices/register', json={'activation_code': code_b, 'mac_address': mac})).status_code, 400)
        self.assertEqual(len((await self.client.get('/api/devices', headers=alice)).json()['devices']), 1)
        self.assertEqual((await self.client.get('/api/devices', headers=bob)).json()['devices'], [])

    async def test_legacy_activation_codes_cannot_register_new_devices(self):
        code = self.devices.create_activation_code('legacy-agent')['code']
        response = await self.client.post('/api/devices/register', json={'activation_code': code, 'mac_address': '00:00:00:00:00:01'})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.devices.get_device_by_mac('00:00:00:00:00:01'))

    async def test_device_self_unbind_does_not_mutate_legacy_store(self):
        token = (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        agent = (await self.client.post('/api/agents', headers=headers, json={'name': 'Alice'})).json()['agent']['id']
        code = (await self.client.post('/api/devices/activation', headers=headers, json={'agent_id': agent})).json()['code']
        result = await self.client.post('/api/devices/register', json={'activation_code': code, 'mac_address': '00:11:22:33:44:55'})
        device_headers = {'Authorization': 'Bearer ' + result.json()['device_token']}
        with patch.object(bridge.platform_store, 'unbind_device') as legacy_unbind:
            response = await self.client.post('/api/devices/me/unbind', headers=device_headers)
        self.assertEqual(response.status_code, 200)
        legacy_unbind.assert_not_called()

    async def test_legacy_device_is_not_activated_by_ota(self):
        code = self.devices.create_activation_code('legacy-agent')['code']
        self.devices.register_device_with_code(code, '00:00:00:00:00:01')
        response = await self.client.post('/xiaozhi/ota/activate', headers={'Device-Id': '00:00:00:00:00:01'})
        self.assertEqual(response.status_code, 202)

    async def test_deleting_agent_revokes_its_device(self):
        token = (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        agent = (await self.client.post('/api/agents', headers=headers, json={'name': 'Alice'})).json()['agent']['id']
        code = (await self.client.post('/api/devices/activation', headers=headers, json={'agent_id': agent})).json()['code']
        device = (await self.client.post('/api/devices/register', json={'activation_code': code, 'mac_address': '00:11:22:33:44:55'})).json()
        self.assertEqual((await self.client.delete('/api/agents/' + agent, headers=headers)).status_code, 200)
        self.assertIsNone(self.devices.get_device_by_mac('00:11:22:33:44:55'))
        self.assertIsNone(self.devices.authenticate_device(device['device_token']))

    async def test_internal_chat_cannot_use_legacy_agent(self):
        with patch.object(bridge.platform_store, 'get_agent', return_value={'id': 'legacy-agent', 'name': 'Legacy'}):
            response = await self.client.post('/api/agents/legacy-agent/v1/chat/completions', headers={'Authorization': 'Bearer ' + bridge.SECRET_KEY}, json={'messages': [{'role': 'user', 'content': 'hello'}]})
        self.assertEqual(response.status_code, 404)

    async def test_runtime_configuration_requires_service_credential(self):
        response = await self.client.post('/config/agent-models', json={'macAddress': '00:00:00:00:00:01'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual((await self.client.post('/config/server-base')).status_code, 401)

    async def test_reported_device_chat_is_saved_only_to_owner(self):
        user_token = (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']
        headers = {'Authorization': 'Bearer ' + user_token}
        agent = (await self.client.post('/api/agents', headers=headers, json={'name': 'Alice'})).json()['agent']['id']
        code = (await self.client.post('/api/devices/activation', headers=headers, json={'agent_id': agent})).json()['code']
        await self.client.post('/api/devices/register', json={'activation_code': code, 'mac_address': '00:11:22:33:44:55'})
        response = await self.client.post('/agent/chat-history/report', headers={'Authorization': 'Bearer ' + bridge.SECRET_KEY}, json={'macAddress': '00:11:22:33:44:55', 'sessionId': 'one', 'chatType': 1, 'content': 'hello'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_agent('alice', agent)['chats'][0]['content'], 'hello')

    async def test_voice_activation_delivers_token_only_to_the_proving_device(self):
        headers = {'Authorization': 'Bearer ' + (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']}
        agent = (await self.client.post('/api/agents', headers=headers, json={'name': 'Alice'})).json()['agent']['id']
        device_headers = {'Device-Id': '00:11:22:33:44:55', 'X-Enrollment-Key': 'a' * 64}
        ota = await self.client.post('/xiaozhi/ota/', headers=device_headers)
        code = ota.json()['activation']['code']
        bound = await self.client.post('/api/devices/activation/bind', headers=headers, json={'agent_id': agent, 'code': code})
        self.assertEqual(bound.status_code, 200)
        self.assertNotIn('device_token', bound.json().get('device', {}))
        rejected = await self.client.post('/xiaozhi/ota/activate', headers={'Device-Id': '00:11:22:33:44:55', 'X-Enrollment-Key': 'b' * 64})
        self.assertNotEqual(rejected.status_code, 200)
        accepted = await self.client.post('/xiaozhi/ota/activate', headers=device_headers)
        self.assertEqual(accepted.status_code, 200)
        token = accepted.json()['device_token']
        identity = await self.client.get('/api/devices/me', headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(identity.json()['agent_id'], agent)

    async def test_legacy_device_can_be_reenrolled_without_deleting_its_record(self):
        code = self.devices.create_activation_code('legacy-agent')['code']
        legacy = self.devices.register_device_with_code(code, '00:11:22:33:44:55')
        user = {'Authorization': 'Bearer ' + (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']}
        agent = (await self.client.post('/api/agents', headers=user, json={'name': 'Alice'})).json()['agent']['id']
        device = {'Device-Id': '00:11:22:33:44:55', 'X-Enrollment-Key': 'c' * 64}
        code = (await self.client.post('/xiaozhi/ota/', headers=device)).json()['activation']['code']
        self.assertEqual((await self.client.post('/api/devices/activation/bind', headers=user, json={'agent_id': agent, 'code': code})).status_code, 200)
        self.assertEqual((await self.client.post('/xiaozhi/ota/activate', headers=device)).json()['agent_id'], agent)
        with self.devices._get_connection() as conn:
            self.assertIsNotNone(conn.execute('SELECT 1 FROM legacy_device_archive WHERE device_id = ?', (legacy['device_id'],)).fetchone())

    async def test_bound_device_cannot_be_claimed_by_another_user(self):
        self.store.create_user('bob', 'another long password')
        async def agent_for(username, password):
            user = {'Authorization': 'Bearer ' + (await self.client.post('/api/auth/login', json={'username': username, 'password': password})).json()['token']}
            agent = (await self.client.post('/api/agents', headers=user, json={'name': username})).json()['agent']['id']
            return user, agent
        alice, agent_a = await agent_for('alice', 'a sufficiently long password')
        bob, agent_b = await agent_for('bob', 'another long password')
        device = {'Device-Id': '00:11:22:33:44:55', 'X-Enrollment-Key': 'a' * 64}
        code = (await self.client.post('/xiaozhi/ota/', headers=device)).json()['activation']['code']
        self.assertEqual((await self.client.post('/api/devices/activation/bind', headers=alice, json={'agent_id': agent_a, 'code': code})).status_code, 200)
        self.assertEqual((await self.client.post('/api/devices/activation/bind', headers=bob, json={'agent_id': agent_b, 'code': code})).status_code, 400)
        self.assertEqual((await self.client.post('/xiaozhi/ota/activate', headers=device)).json()['agent_id'], agent_a)

    async def test_console_chat_history_survives_reload(self):
        token = (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        agent = (await self.client.post('/api/agents', headers=headers, json={'name': 'Alice'})).json()['agent']['id']
        chats = [{'id': 'one', 'title': 'Conversation', 'createdAt': 'today', 'messageCount': 1, 'messages': [{'id': 'message', 'role': 'user', 'content': 'hello', 'createdAt': 'today'}]}]
        self.assertEqual((await self.client.put('/api/agents/' + agent, headers=headers, json={'chats': chats})).status_code, 200)
        self.assertEqual((await self.client.get('/api/agents/' + agent, headers=headers)).json()['chats'], chats)

    async def test_enrollment_rejects_missing_or_malformed_device_identity(self):
        self.assertEqual((await self.client.post('/xiaozhi/ota/', headers={'X-Enrollment-Key': 'a' * 64})).status_code, 400)
        self.assertEqual((await self.client.post('/xiaozhi/ota/', headers={'Device-Id': 'not-a-mac', 'X-Enrollment-Key': 'a' * 64})).status_code, 400)

    async def test_successful_registration_does_not_reset_guess_limit(self):
        auth = {'Authorization': 'Bearer ' + (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']}
        agent = (await self.client.post('/api/agents', headers=auth, json={'name': 'Alice'})).json()['agent']['id']
        for cycle in range(2):
            for attempt in range(4):
                result = await self.client.post('/api/devices/register', json={'activation_code': f'9999{cycle}{attempt}', 'mac_address': f'02:00:00:00:{cycle:02X}:{attempt:02X}'})
                self.assertEqual(result.status_code, 400)
            code = (await self.client.post('/api/devices/activation', headers=auth, json={'agent_id': agent})).json()['code']
            result = await self.client.post('/api/devices/register', json={'activation_code': code, 'mac_address': f'02:00:00:00:{cycle:02X}:FF'})
            self.assertEqual(result.status_code, 200)
        result = await self.client.post('/api/devices/register', json={'activation_code': '999999', 'mac_address': '02:00:00:00:FF:FF'})
        self.assertEqual(result.status_code, 429)

    async def test_successful_binding_does_not_reset_guess_limit(self):
        auth = {'Authorization': 'Bearer ' + (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']}
        agent = (await self.client.post('/api/agents', headers=auth, json={'name': 'Alice'})).json()['agent']['id']
        for cycle in range(2):
            for attempt in range(4):
                result = await self.client.post('/api/devices/activation/bind', headers=auth, json={'agent_id': agent, 'code': f'9999{cycle}{attempt}'})
                self.assertEqual(result.status_code, 400)
            device = {'Device-Id': f'02:00:00:00:00:{cycle:02X}', 'X-Enrollment-Key': f'{cycle + 1:064x}'}
            code = (await self.client.post('/xiaozhi/ota/', headers=device)).json()['activation']['code']
            result = await self.client.post('/api/devices/activation/bind', headers=auth, json={'agent_id': agent, 'code': code})
            self.assertEqual(result.status_code, 200)
        result = await self.client.post('/api/devices/activation/bind', headers=auth, json={'agent_id': agent, 'code': '999999'})
        self.assertEqual(result.status_code, 429)

    async def test_thingsboard_rejects_unapproved_and_malformed_origins(self):
        token = (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        agent = (await self.client.post('/api/agents', headers=headers, json={'name': 'Alice'})).json()['agent']['id']
        with patch.dict('os.environ', {'THINGSBOARD_ALLOWED_ORIGINS': 'https://approved.example,http://192.168.1.20:8080'}):
            for host in ('http://internal-service/sensitive?x=', 'http://169.254.169.254', 'https://approved.example@evil.example', 'https://approved.example/path'):
                with self.subTest(host=host):
                    result = await self.client.put('/api/agents/' + agent, headers=headers, json={'integrations': {'thingsboard': {'host': host}}})
                    self.assertEqual(result.status_code, 400)
                    result = await self.client.post('/api/integrations/thingsboard/test', headers=headers, json={'agent_id': agent, 'host': host, 'api_key': 'key'})
                    self.assertEqual(result.status_code, 400)
            result = await self.client.put('/api/agents/' + agent, headers=headers, json={'integrations': {'thingsboard': {'host': 'http://192.168.1.20:8080'}}})
            self.assertEqual(result.status_code, 200)

    async def test_reenrollment_archives_revoked_legacy_record(self):
        code = self.devices.create_activation_code('legacy-agent')['code']
        legacy = self.devices.register_device_with_code(code, '00:11:22:33:44:55')
        self.devices.unbind_device_by_token(legacy['device_token'])
        auth = {'Authorization': 'Bearer ' + (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']}
        agent = (await self.client.post('/api/agents', headers=auth, json={'name': 'Alice'})).json()['agent']['id']
        device = {'Device-Id': '00:11:22:33:44:55', 'X-Enrollment-Key': 'c' * 64}
        code = (await self.client.post('/xiaozhi/ota/', headers=device)).json()['activation']['code']
        self.assertEqual((await self.client.post('/api/devices/activation/bind', headers=auth, json={'agent_id': agent, 'code': code})).status_code, 200)
        with self.devices._get_connection() as conn:
            row = conn.execute('SELECT data FROM legacy_device_archive WHERE device_id = ?', (legacy['device_id'],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(__import__('json').loads(row['data'])['status'], 'revoked')

    async def test_code_registration_archives_revoked_legacy_record(self):
        code = self.devices.create_activation_code('legacy-agent')['code']
        legacy = self.devices.register_device_with_code(code, '00:11:22:33:44:55')
        self.devices.unbind_device_by_token(legacy['device_token'])
        auth = {'Authorization': 'Bearer ' + (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']}
        agent = (await self.client.post('/api/agents', headers=auth, json={'name': 'Alice'})).json()['agent']['id']
        code = (await self.client.post('/api/devices/activation', headers=auth, json={'agent_id': agent})).json()['code']
        self.assertEqual((await self.client.post('/api/devices/register', json={'activation_code': code, 'mac_address': '00:11:22:33:44:55'})).status_code, 200)
        with self.devices._get_connection() as conn:
            row = conn.execute('SELECT data FROM legacy_device_archive WHERE device_id = ?', (legacy['device_id'],)).fetchone()
        self.assertIsNotNone(row)

    async def test_ota_requires_device_token_before_revealing_binding(self):
        auth = {'Authorization': 'Bearer ' + (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']}
        agent = (await self.client.post('/api/agents', headers=auth, json={'name': 'Alice'})).json()['agent']['id']
        code = (await self.client.post('/api/devices/activation', headers=auth, json={'agent_id': agent})).json()['code']
        device = (await self.client.post('/api/devices/register', json={'activation_code': code, 'mac_address': '00:11:22:33:44:55'})).json()
        headers = {'Device-Id': '00:11:22:33:44:55', 'X-Enrollment-Key': 'd' * 64}
        anonymous = await self.client.post('/xiaozhi/ota/', headers=headers)
        self.assertIn('activation', anonymous.json())
        bound = await self.client.post('/xiaozhi/ota/', headers={**headers, 'Authorization': 'Bearer ' + device['device_token']})
        self.assertEqual(bound.status_code, 200)
        self.assertNotIn('activation', bound.json())

    async def test_nested_integration_cannot_bypass_origin_validation(self):
        token = (await self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'a sufficiently long password'})).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        agent = (await self.client.post('/api/agents', headers=headers, json={'name': 'Alice'})).json()['agent']['id']
        payload = {'integrations': {'thingsboard': {}}, 'config': {'integrations': {'thingsboard': {'host': 'http://internal-service'}}}}
        self.assertEqual((await self.client.put('/api/agents/' + agent, headers=headers, json=payload)).status_code, 400)

    async def test_legacy_browser_tester_is_not_served_on_console_origin(self):
        self.assertEqual((await self.client.get('/test/test_page.html')).status_code, 404)
        self.assertEqual((await self.client.get('/pc-test')).status_code, 404)
