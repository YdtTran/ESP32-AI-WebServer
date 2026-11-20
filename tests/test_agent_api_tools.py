import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

api_tools = importlib.import_module('platform-bridge.agent_api_tools')


class AgentApiToolsTests(unittest.IsolatedAsyncioTestCase):
    def test_matching_device_normalizes_separators_when_unique(self):
        devices = [
            {'name': 'Simulated Tasmota A', 'label': None, 'id': {'id': 'id-a'}},
            {'name': 'Simulated Tasmota B', 'label': None, 'id': {'id': 'id-b'}},
            {'name': 'relay-1', 'label': '', 'id': {'id': 'id-r1'}},
            {'name': 'relay 1', 'label': '', 'id': {'id': 'id-r2'}},
        ]
        self.assertEqual(api_tools._matching_device(devices, 'simulated-tasmota-B')['id']['id'], 'id-b')
        self.assertEqual(api_tools._matching_device(devices, 'Simulated_Tasmota_A')['id']['id'], 'id-a')
        self.assertIsNone(api_tools._matching_device(devices, 'relay_1'))
        self.assertIsNone(api_tools._matching_device(devices, 'simulated-tasmota'))

    async def test_unknown_uuid_returns_device_not_found_with_device_list(self):
        client = AsyncMock()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {'data': [{'name': 'Simulated Tasmota B', 'label': None, 'id': {'id': '6317f9e0-e1b2-11f1-8712-afcc819282f5'}}], 'hasNext': False}
        client.get.return_value = resp
        with patch.object(api_tools, 'authorize_rpc', AsyncMock(return_value='RPC not sent: capability discovery or parameter validation failed (Device not found)')):
            result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'},
                'execute_device_action', {'device_identifier': '6303ae91-e1b2-11f1-8712-afcc819282f5', 'method': 'setPower', 'params': {'state': True}},
                profile_id='agent-a')
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'device_not_found')
        self.assertEqual(result['devices'][0]['name'], 'Simulated Tasmota B')

    async def test_rpc_is_not_sent_without_capability_authorization(self):
        client = AsyncMock()
        with patch.object(api_tools, 'authorize_rpc', AsyncMock(return_value='RPC method is not registered')):
            result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'},
                'thingsboard_rpc', {'device_id': '11111111-2222-3333-4444-555555555555',
                                    'method': 'invented'}, profile_id='agent-a')
        self.assertFalse(result['success'])
        self.assertIn('not registered', result['error'])
        client.post.assert_not_awaited()

    async def test_rpc_fails_closed_when_registry_is_unavailable(self):
        client = AsyncMock()
        with patch.object(api_tools.importlib, 'import_module', side_effect=ImportError('missing registry')):
            result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'},
                'thingsboard_rpc', {'device_id': '11111111-2222-3333-4444-555555555555',
                                    'method': 'setPower'}, profile_id='agent-a')
        self.assertFalse(result['success'])
        client.post.assert_not_awaited()

    async def test_rpc_rejects_non_boolean_delivery_mode(self):
        client = AsyncMock()
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'},
            'thingsboard_rpc', {'device_id': '11111111-2222-3333-4444-555555555555',
                                'method': 'setPower', 'two_way': 'false'}, profile_id='agent-a')
        self.assertFalse(result['success'])
        self.assertIn('boolean', result['error'])
        client.post.assert_not_awaited()

    @patch.object(api_tools, 'authorize_rpc', new_callable=AsyncMock, return_value=None)
    async def test_device_error_is_not_reported_as_success(self, authorize):
        client = AsyncMock()
        client.post.return_value.status_code = 200
        client.post.return_value.json = lambda: {'error': 'relay rejected command'}
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'},
            'thingsboard_rpc', {'device_id': '11111111-2222-3333-4444-555555555555',
                                'method': 'setPower'}, profile_id='agent-a')
        self.assertFalse(result['success'])

    async def test_read_uses_agent_api_key_and_returns_data(self):
        response = AsyncMock()
        response.status_code = 200
        response.json = lambda: {'temperature': [{'value': '25'}]}
        client = AsyncMock()
        client.get.return_value = response
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'}, 'thingsboard_get', {'path': '/api/plugins/telemetry/DEVICE/123/values/timeseries'})
        self.assertEqual(result['data']['temperature'][0]['value'], '25')
        self.assertEqual(client.get.await_args.kwargs['headers']['X-Authorization'], 'ApiKey private')

    async def test_rejects_external_url_and_path_traversal(self):
        client = AsyncMock()
        for path in ('https://other.test/api/device', '/api/../admin', '/api/%2e%2e/admin', '/api/%252e%252e/admin'):
            result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'}, 'thingsboard_get', {'path': path})
            self.assertFalse(result['success'])
        client.get.assert_not_awaited()

    async def test_agents_use_their_own_keys_and_hosts(self):
        response = AsyncMock()
        response.status_code = 200
        response.json = lambda: {'data': []}
        client = AsyncMock()
        client.get.return_value = response
        for host, key in [('http://a.test', 'key-a'), ('http://b.test', 'key-b')]:
            await api_tools.execute(client, {'host': host, 'api_key': key}, 'thingsboard_get', {'path': '/api/tenant/devices'})
        self.assertEqual(client.get.await_args_list[0].args[0], 'http://a.test/api/tenant/devices')
        self.assertEqual(client.get.await_args_list[0].kwargs['headers']['X-Authorization'], 'ApiKey key-a')
        self.assertEqual(client.get.await_args_list[1].args[0], 'http://b.test/api/tenant/devices')
        self.assertEqual(client.get.await_args_list[1].kwargs['headers']['X-Authorization'], 'ApiKey key-b')

    async def test_authentication_data_is_not_exposed_to_model(self):
        response = AsyncMock()
        response.status_code = 200
        response.json = lambda: {'name': 'sensor1', 'apiKey': 'secret', 'nested': {'password': 'secret'}}
        client = AsyncMock()
        client.get.return_value = response
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'}, 'thingsboard_get', {'path': '/api/device/123'})
        self.assertNotIn('secret', str(result))
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'}, 'thingsboard_get', {'path': '/api/device/123/credentials'})
        self.assertFalse(result['success'])
        self.assertEqual(client.get.await_count, 1)

    @patch.object(api_tools, 'authorize_rpc', new_callable=AsyncMock, return_value=None)
    async def test_rpc_passes_method_and_parameters_and_reports_failure(self, authorize):
        response = AsyncMock()
        response.status_code = 504
        response.text = 'timeout'
        client = AsyncMock()
        client.post.return_value = response
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'}, 'thingsboard_rpc', {'device_id': '11111111-2222-3333-4444-555555555555', 'method': 'setPower', 'params': False, 'two_way': True})
        self.assertFalse(result['success'])
        self.assertEqual(client.post.await_args.kwargs['json']['params'], False)
        self.assertEqual(result['status'], 504)

    async def test_read_queries_use_allowlisted_post_endpoints_only(self):
        response = AsyncMock()
        response.status_code = 200
        response.json = lambda: {'data': []}
        client = AsyncMock()
        client.post.return_value = response
        config = {'host': 'http://tb.test', 'api_key': 'private'}
        result = await api_tools.execute(client, config, 'thingsboard_query', {'path': '/api/entitiesQuery/find', 'body': {'entityFilter': {'type': 'entityType', 'entityType': 'DEVICE'}}})
        self.assertTrue(result['success'])
        self.assertEqual(client.post.await_args.kwargs['json']['entityFilter']['entityType'], 'DEVICE')
        result = await api_tools.execute(client, config, 'thingsboard_query', {'path': '/api/device', 'body': {}})
        self.assertFalse(result['success'])
        self.assertEqual(client.post.await_count, 1)

    @patch.object(api_tools, 'authorize_rpc', new_callable=AsyncMock, return_value=None)
    async def test_two_way_rpc_honors_device_timeout(self, authorize):
        response = AsyncMock()
        response.status_code = 200
        response.json = lambda: {'result': 'ok'}
        client = AsyncMock()
        client.post.return_value = response
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'}, 'thingsboard_rpc', {'device_id': '11111111-2222-3333-4444-555555555555', 'method': 'calibrate', 'timeout_ms': 15000})
        self.assertTrue(result['success'])
        self.assertEqual(client.post.await_args.kwargs['json']['timeout'], 15000)
        self.assertGreater(client.post.await_args.kwargs['timeout'], 15)

    @patch.object(api_tools, 'authorize_rpc', new_callable=AsyncMock, return_value=None)
    async def test_registered_rpc_supports_one_way_delivery(self, authorize):
        response = AsyncMock()
        response.status_code = 204
        response.json = lambda: (_ for _ in ()).throw(ValueError())
        response.text = ''
        client = AsyncMock()
        client.post.return_value = response
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'}, 'thingsboard_rpc', {'device_id': '11111111-2222-3333-4444-555555555555', 'method': 'calibrate', 'params': {'offset': 2}, 'two_way': False})
        self.assertTrue(result['success'])
        self.assertTrue(client.post.await_args.args[0].endswith('/api/rpc/oneway/11111111-2222-3333-4444-555555555555'))
        self.assertEqual(client.post.await_args.kwargs['json'], {'method': 'calibrate', 'params': {'offset': 2}})

    async def test_read_only_policy_prevents_rpc(self):
        client = AsyncMock()
        result = await api_tools.execute(client, {'host': 'http://tb.test', 'api_key': 'private'}, 'thingsboard_rpc', {'device_id': '11111111-2222-3333-4444-555555555555', 'method': 'setPower'}, read_only=True)
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'forbidden')
        client.post.assert_not_awaited()

    @patch.object(api_tools, 'authorize_rpc', new_callable=AsyncMock, return_value=None)
    async def test_two_way_confirmed_offline_and_timeout_error_codes(self, authorize):
        device_id = '11111111-2222-3333-4444-555555555555'
        config = {'host': 'http://tb.test', 'api_key': 'private'}

        ok_resp = MagicMock(status_code=200)
        ok_resp.json.return_value = {'POWER': 'ON'}
        offline_resp = MagicMock(status_code=200)
        offline_resp.json.return_value = {'error': 'Device offline or awaiting manual recovery'}
        timeout_resp = MagicMock(status_code=504, text='timeout')

        client = AsyncMock()
        client.post.side_effect = [ok_resp, offline_resp, timeout_resp, httpx.TimeoutException('read timed out')]

        ok = await api_tools.execute(client, config, 'thingsboard_rpc',
            {'device_id': device_id, 'method': 'setPower', 'params': {'state': True}, 'two_way': True}, profile_id='agent-a')
        self.assertTrue(ok['success'])
        self.assertTrue(ok['confirmed'])
        self.assertEqual(ok['delivery'], 'device_response')

        offline = await api_tools.execute(client, config, 'thingsboard_rpc',
            {'device_id': device_id, 'method': 'setPower', 'params': {'state': True}, 'two_way': True}, profile_id='agent-a')
        self.assertFalse(offline['success'])
        self.assertFalse(offline['confirmed'])
        self.assertEqual(offline['error_code'], 'device_offline')

        t504 = await api_tools.execute(client, config, 'thingsboard_rpc',
            {'device_id': device_id, 'method': 'setPower', 'params': {'state': True}, 'two_way': True}, profile_id='agent-a')
        self.assertFalse(t504['success'])
        self.assertEqual(t504['error_code'], 'timeout')

        texc = await api_tools.execute(client, config, 'thingsboard_rpc',
            {'device_id': device_id, 'method': 'setPower', 'params': {'state': True}, 'two_way': True}, profile_id='agent-a')
        self.assertFalse(texc['success'])
        self.assertEqual(texc['error_code'], 'timeout')

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'requires jsonschema')
    async def test_capability_discovery_validation_and_policy_with_real_registry(self):
        dev_a = '11111111-2222-3333-4444-555555555551'
        dev_b = '11111111-2222-3333-4444-555555555552'
        caps_a = {'methods': {
            'setPower': {'description': 'Set power', 'params': {'type': 'object', 'properties': {'state': {'type': 'boolean'}}, 'required': ['state'], 'additionalProperties': False}},
            'getPower': {'description': 'Get power', 'params': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
            'togglePower': {'description': 'Toggle power', 'params': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
        }}
        caps_b = {'methods': {
            'setPower': caps_a['methods']['setPower'],
            'getPower': caps_a['methods']['getPower'],
        }}

        def make_resp(data, status=200):
            r = MagicMock(status_code=status, content=json.dumps(data).encode(), text=json.dumps(data))
            r.json.return_value = data
            r.raise_for_status = lambda: None
            return r

        async def mock_get(url, **kwargs):
            if '/api/tenant/devices' in url:
                return make_resp({'data': [
                    {'id': {'id': dev_a}, 'name': 'Simulated Tasmota A', 'type': 'default'},
                    {'id': {'id': dev_b}, 'name': 'Simulated Tasmota B', 'type': 'default'},
                ], 'hasNext': False})
            if f'/{dev_a}/values/attributes/CLIENT_SCOPE' in url:
                return make_resp([{'key': 'rpcCapabilities', 'value': caps_a}])
            if f'/{dev_b}/values/attributes/CLIENT_SCOPE' in url:
                return make_resp([{'key': 'rpcCapabilities', 'value': caps_b}])
            if '/values/attributes/' in url:
                return make_resp([])
            if '/api/tenant/dashboards' in url or '/api/ruleChains' in url:
                return make_resp({'data': [], 'hasNext': False})
            return make_resp({})

        async def mock_post(url, **kwargs):
            return make_resp({'POWER': 'ON'}, 200)

        http = AsyncMock()
        http.get.side_effect = mock_get
        http.post.side_effect = mock_post
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=http)
        context.__aexit__ = AsyncMock(return_value=False)

        config = {'host': 'http://tb.test', 'api_key': 'key-a'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PLATFORM_DATA_DIR': tmp}), \
                patch('httpx.AsyncClient', return_value=context):
            listed = await api_tools.execute(http, config, 'list_available_rpc',
                {'device_identifier': 'Simulated Tasmota A'}, profile_id='agent-a')
            self.assertTrue(listed['success'])
            self.assertEqual({c['method'] for c in listed['capabilities']}, {'setPower', 'getPower', 'togglePower'})

            bad_method = await api_tools.execute(http, config, 'execute_device_action',
                {'device_identifier': 'Simulated Tasmota B', 'method': 'togglePower', 'params': {}}, profile_id='agent-a')
            self.assertFalse(bad_method['success'])
            self.assertEqual(bad_method['error_code'], 'unsupported_method')
            self.assertIn('setPower', bad_method['error'])
            self.assertIn('"state"', bad_method['error'])

            bad_params = await api_tools.execute(http, config, 'execute_device_action',
                {'device_identifier': 'Simulated Tasmota A', 'method': 'setPower', 'params': {'state': 'yes'}}, profile_id='agent-a')
            self.assertFalse(bad_params['success'])
            self.assertEqual(bad_params['error_code'], 'invalid_params')
            self.assertIn('Available methods for this device', bad_params['error'])

            denied_ro = await api_tools.execute(http, config, 'execute_device_action',
                {'device_identifier': 'Simulated Tasmota A', 'method': 'setPower', 'params': {'state': True}},
                read_only=True, profile_id='agent-ro')
            self.assertFalse(denied_ro['success'])
            self.assertEqual(denied_ro['error_code'], 'forbidden')

            ok = await api_tools.execute(http, config, 'execute_device_action',
                {'device_identifier': 'Simulated Tasmota A', 'method': 'setPower', 'params': {'state': True}}, profile_id='agent-a')
            self.assertTrue(ok['success'])
            self.assertTrue(ok['confirmed'])
            self.assertEqual(ok['delivery'], 'device_response')
            self.assertEqual(ok['response'], {'POWER': 'ON'})
