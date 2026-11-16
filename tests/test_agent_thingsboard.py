import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

bridge = importlib.import_module('platform-bridge.app')


class AgentThingsBoardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.allowed = patch.dict('os.environ', {'THINGSBOARD_ALLOWED_ORIGINS': 'http://tb.test'})
        self.allowed.start()
        self.addCleanup(self.allowed.stop)

    async def test_unknown_rpc_device_returns_names_for_model_to_resolve(self):
        agent = {'id': 'test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data):
            result = MagicMock(status_code=200)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'send_rpc', 'arguments': '{"device_name":"Simulated power switch for MCP Test Device","method":"turnOff"}'}}]}}]}), response({'choices': [{'message': {'content': 'checking'}}]})]
        client.get.return_value = response({'data': [{'name': 'MCP Test Device', 'label': 'MCP test only', 'id': {'id': 'device'}}]})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='turn off MCP Test Device'))
        self.assertIn('MCP Test Device', result['tool_calls'][0]['result'])
        self.assertIn('exact device', result['tool_calls'][0]['result'])
        self.assertEqual(client.post.await_count, 2)

    async def test_undocumented_rpc_method_is_not_sent(self):
        agent = {'id': 'test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data):
            result = MagicMock(status_code=200)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'send_rpc', 'arguments': '{"device_name":"MCP Test Device","method":"turnOff"}'}}]}}]}), response({'choices': [{'message': {'content': 'unsupported'}}]})]
        client.get.return_value = response({'data': [{'name': 'MCP Test Device', 'id': {'id': '11111111-2222-3333-4444-555555555555'}}]})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context), patch.object(bridge.agent_api_tools, 'authorize_rpc',
                new=AsyncMock(return_value='RPC not sent: capability validation failed.')):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='turn off MCP Test Device'))
        self.assertIn('capability validation failed', result['tool_calls'][0]['result'])
        self.assertEqual(client.post.await_count, 2)

    async def test_legacy_rpc_uses_validated_one_way_route(self):
        agent = {'id': 'test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        device_id = '11111111-2222-3333-4444-555555555555'
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'tool_calls': [{'id': 'one', 'function': {
            'name': 'send_rpc', 'arguments': '{"device_name":"Relay","method":"setPower","params":"{\\"state\\":true}"}'}}]}}]}),
            response({}, 204), response({'choices': [{'message': {'content': 'submitted'}}]})]
        client.get.return_value = response({'data': [{'name': 'Relay', 'id': {'id': device_id}}]})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context), patch.object(bridge.agent_api_tools, 'authorize_rpc',
                new=AsyncMock(return_value=None)) as authorize:
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='turn on Relay'))
        self.assertEqual(authorize.await_args.args[1:], ('test', device_id, 'setPower', {'state': True}))
        self.assertIn('submitted', result['tool_calls'][0]['result'])

    async def test_missing_device_name_does_not_target_single_device(self):
        agent = {'id': 'test', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data):
            result = MagicMock(status_code=200)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'send_rpc', 'arguments': '{"method":"setPower","params":"true"}'}}]}}]}), response({'choices': [{'message': {'content': 'device required'}}]})]
        client.get.return_value = response({'data': [{'name': 'sensor1', 'label': '', 'id': {'id': 'device'}}]})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='set power'))
        self.assertIn('not found', result['tool_calls'][0]['result'])
        self.assertEqual(client.post.await_count, 2)

    async def test_unknown_sensor_does_not_return_another_devices_data(self):
        agent = {'id': 'test', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'test'}}}
        model_reply = MagicMock()
        model_reply.json.return_value = {'choices': [{'message': {'content': '', 'tool_calls': [{'id': 'call1', 'function': {'name': 'get_telemetry', 'arguments': '{"device_name":"missing"}'}}]}}]}
        final_reply = MagicMock()
        final_reply.json.return_value = {'choices': [{'message': {'content': 'not found'}}]}
        devices = MagicMock(status_code=200)
        devices.json.return_value = {'data': [{'name': 'other', 'id': {'id': 'other-id'}}]}
        client = AsyncMock()
        client.post.side_effect = [model_reply, final_reply]
        client.get.return_value = devices
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch.object(bridge.platform_store, 'sync_to_agent_profile_json'), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='read missing'))
        self.assertIn('not found', result['tool_calls'][0]['result'])
        self.assertEqual(client.get.await_count, 1)

    async def test_tool_network_errors_are_returned_to_model(self):
        agent = {'id': 'test', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data):
            result = MagicMock(status_code=200)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'list_devices', 'arguments': '{}'}}]}}]}), response({'choices': [{'message': {'content': 'ThingsBoard is unavailable.'}}]})]
        client.get.side_effect = OSError('network down')
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='list sensors'))
        self.assertEqual(result['reply'], 'ThingsBoard is unavailable.')
        self.assertIn('error', result['tool_calls'][0]['result'])

    async def test_customer_api_key_can_list_its_devices(self):
        agent = {'id': 'test', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'list_devices', 'arguments': '{}'}}]}}]}), response({'choices': [{'message': {'content': 'done'}}]})]
        client.get.side_effect = [response({}, 403), response({'authority': 'CUSTOMER_USER', 'customerId': {'id': '11111111-2222-3333-4444-555555555555'}}), response({'data': [{'name': 'sensor1', 'id': {'id': 'device'}}]})]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='list sensors'))
        self.assertIn('sensor1', result['tool_calls'][0]['result'])
        self.assertIn('/api/customer/11111111-2222-3333-4444-555555555555/devices', client.get.await_args.args[0])

    async def test_existing_sensor_with_no_telemetry_is_not_missing(self):
        agent = {'id': 'test', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data):
            result = MagicMock(status_code=200)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'get_telemetry', 'arguments': '{"device_name":"sensor1"}'}}]}}]}), response({'choices': [{'message': {'content': 'no data'}}]})]
        client.get.side_effect = [response({'data': [{'name': 'sensor1', 'id': {'id': 'sensor-id'}}]}), response({})]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='read sensor'))
        self.assertIn('found', result['tool_calls'][0]['result'])
        self.assertIn('no telemetry', result['tool_calls'][0]['result'])

    async def test_promise_to_fetch_after_device_listing_is_retried(self):
        agent = {'id': 'test', 'name': 'Test', 'language': 'vi', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data):
            result = MagicMock(status_code=200)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'list_devices', 'arguments': '{}'}}]}}]}), response({'choices': [{'message': {'role': 'assistant', 'content': 'Tôi sẽ lấy thông số môi trường từ đó.'}}]}), response({'choices': [{'message': {'role': 'assistant', 'content': 'Không có dữ liệu.'}}]})]
        client.get.return_value = response({'data': [{'name': 'sensor1', 'id': {'id': 'device'}}]})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='read sensor'))
        self.assertEqual(result['reply'], 'Không có dữ liệu.')
        self.assertEqual(client.post.await_count, 3)

    async def test_waiting_without_tool_call_is_retried(self):
        agent = {'id': 'test', 'name': 'Test', 'language': 'vi', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def reply(content):
            response = MagicMock()
            response.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': content}}]}
            return response
        client = AsyncMock()
        client.post.side_effect = [reply('Đang lấy thông tin. Hãy đợi một chút.'), reply('Tôi không thể lấy thông tin lúc này.')]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='read sensor'))
        self.assertEqual(client.post.await_count, 2)
        self.assertEqual(result['reply'], 'Tôi không thể lấy thông tin lúc này.')

    async def test_legacy_sensor_tool_searches_all_device_pages(self):
        agent = {'id': 'test', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data):
            result = MagicMock(status_code=200)
            result.json.return_value = data
            return result
        model = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'get_telemetry', 'arguments': '{"device_name":"sensor1"}'}}]}}]}), response({'choices': [{'message': {'content': 'done'}}]})]
        client = AsyncMock()
        client.post.side_effect = model
        client.get.side_effect = [response({'data': [], 'hasNext': True}), response({'data': [{'name': 'sensor1', 'id': {'id': 'sensor-id'}}], 'hasNext': False}), response({'temperature': [{'value': '25'}]})]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch.object(bridge.platform_store, 'sync_to_agent_profile_json'), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='read sensor'))
        self.assertIn('25', result['tool_calls'][0]['result'])
        self.assertEqual(client.get.await_count, 3)

    async def test_generic_telemetry_resolves_device_name_before_request(self):
        agent = {'id': 'test', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data):
            result = MagicMock(status_code=200)
            result.json.return_value = data
            return result
        client = AsyncMock()
        client.post.side_effect = [response({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'thingsboard_get', 'arguments': '{"path":"/api/plugins/telemetry/DEVICE/sensor1/values/attributes"}'}}]}}]}), response({'choices': [{'message': {'content': 'done'}}]})]
        client.get.side_effect = [response({'data': [{'name': 'sensor1', 'id': {'id': '11111111-2222-3333-4444-555555555555'}}]}), response([{'key': 'active', 'value': True}])]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='read attributes'))
        self.assertIn('active', result['tool_calls'][0]['result'])
        self.assertIn('/DEVICE/11111111-2222-3333-4444-555555555555/', client.get.await_args.args[0])

    async def test_model_can_call_multiple_tools_with_agent_key(self):
        agent = {'id': 'test', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def reply(message):
            response = MagicMock()
            response.json.return_value = {'choices': [{'message': message}]}
            return response
        model = [
            reply({'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'one', 'function': {'name': 'thingsboard_get', 'arguments': '{"path":"/api/tenant/devices?pageSize=10&page=0"}'}}]}),
            reply({'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'two', 'function': {'name': 'thingsboard_get', 'arguments': '{"path":"/api/plugins/telemetry/DEVICE/111/values/timeseries"}'}}]}),
            reply({'role': 'assistant', 'content': 'Temperature is 25.'}),
        ]
        tb_response = MagicMock(status_code=200)
        tb_response.json.return_value = {'temperature': [{'value': '25'}]}
        client = AsyncMock()
        client.post.side_effect = model
        client.get.return_value = tb_response
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch.object(bridge.platform_store, 'sync_to_agent_profile_json'), patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='read sensor'))
        self.assertEqual(result['reply'], 'Temperature is 25.')
        self.assertEqual(len(result['tool_calls']), 2)
        self.assertEqual(client.get.await_count, 2)
        self.assertEqual(client.get.await_args.kwargs['headers']['X-Authorization'], 'ApiKey private')


if __name__ == '__main__':
    unittest.main()
