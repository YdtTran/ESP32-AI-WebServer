import importlib
import unittest
from unittest.mock import AsyncMock, patch

api_tools = importlib.import_module('platform-bridge.agent_api_tools')


class AgentApiToolsTests(unittest.IsolatedAsyncioTestCase):
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
        client.post.assert_not_awaited()
