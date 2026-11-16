import asyncio
import importlib
import importlib.util
import json
import os
import tempfile
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

    async def test_concurrent_agents_stay_isolated_and_enforce_policies(self):
        dev_a = '11111111-2222-3333-4444-555555555551'
        agent_a = {'id': 'agent-a', 'name': 'Alpha', 'tool_policy': 'full_control',
                   'integrations': {'thingsboard': {'host': 'http://tb-a.test', 'api_key': 'key-a'}}}
        agent_b = {'id': 'agent-b', 'name': 'Beta', 'tool_policy': 'read_only_telemetry',
                   'integrations': {'thingsboard': {'host': 'http://tb-b.test', 'api_key': 'key-b'}}}

        def resp(data, status=200):
            r = MagicMock(status_code=status)
            r.json.return_value = data
            r.raise_for_status = lambda: None
            return r

        async def mock_post(url, **kwargs):
            if url.endswith('/v1/chat/completions'):
                user_msg = kwargs['json']['messages'][-1]['content'] if kwargs['json']['messages'][-1]['role'] == 'user' else None
                if user_msg == 'turn on alpha':
                    return resp({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'a1', 'function': {
                        'name': 'execute_device_action',
                        'arguments': json.dumps({'device_identifier': dev_a, 'method': 'setPower', 'params': {'state': True}})
                    }}]}}]})
                if user_msg == 'turn on beta':
                    return resp({'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'b1', 'function': {
                        'name': 'execute_device_action',
                        'arguments': json.dumps({'device_identifier': dev_a, 'method': 'setPower', 'params': {'state': True}})
                    }}]}}]})
                return resp({'choices': [{'message': {'content': 'done'}}]})
            if url.startswith('http://tb-a.test/api/rpc/twoway/'):
                self.assertEqual(kwargs['headers']['X-Authorization'], 'ApiKey key-a')
                return resp({'POWER': 'ON'}, 200)
            self.fail(f'Unexpected POST to {url}')

        client = AsyncMock()
        client.post.side_effect = mock_post
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)

        with patch.dict('os.environ', {'THINGSBOARD_ALLOWED_ORIGINS': 'http://tb-a.test,http://tb-b.test'}), \
                patch('httpx.AsyncClient', return_value=context), \
                patch.object(bridge.agent_api_tools, 'authorize_rpc', new=AsyncMock(return_value=None)):
            res_a, res_b = await asyncio.gather(
                bridge.run_agent_chat(agent_a, bridge.AgentChatRequest(message='turn on alpha')),
                bridge.run_agent_chat(agent_b, bridge.AgentChatRequest(message='turn on beta')),
            )

        tc_a = json.loads(res_a['tool_calls'][0]['result'])
        tc_b = json.loads(res_b['tool_calls'][0]['result'])
        self.assertTrue(tc_a['success'])
        self.assertTrue(tc_a['confirmed'])
        self.assertFalse(tc_b['success'])
        self.assertEqual(tc_b['error_code'], 'forbidden')

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'requires jsonschema')
    async def test_console_agent_chat_to_mcp_to_tasmota_e2e_workflow(self):
        from pathlib import Path
        from tools.tasmota_bridge import TasmotaBridge, device_capabilities
        from tools.virtual_tasmota import power_command

        dev_a = '11111111-2222-3333-4444-555555555551'
        dev_b = '11111111-2222-3333-4444-555555555552'
        cfg = json.loads((Path(__file__).resolve().parents[1] / 'tools/tasmota-bridge.example.json').read_text())
        relay_power = {'relay-a': False, 'relay-b': False}
        tb_attributes = {
            dev_a: {'rpcCapabilities': device_capabilities(cfg['devices']['relay-a'])},
            dev_b: {'rpcCapabilities': device_capabilities(cfg['devices']['relay-b'])},
        }
        tb_telemetry = {'Simulated Tasmota A': {'POWER': [{'value': 'OFF', 'ts': 1000}]}}
        rpc_replies = []
        seq = {'id': 0}

        def on_mqtt_command(topic, payload):
            relay_key = 'relay-a' if 'relay-a' in topic else 'relay-b'
            relay_power[relay_key] = power_command(payload, relay_power[relay_key])
            state_str = 'ON' if relay_power[relay_key] else 'OFF'
            tb_telemetry[cfg['devices'][relay_key]['name']] = {'POWER': [{'value': state_str, 'ts': 2000}]}
            tasmota_bridge.result(relay_key, {'POWER': state_str})

        tasmota_bridge = TasmotaBridge(cfg['devices'], on_mqtt_command, rpc_replies.append)
        tasmota_bridge.set_online('relay-a', True)
        tasmota_bridge.set_online('relay-b', True)

        agent = {'id': 'agent-e2e', 'name': 'E2E Console Agent', 'language': 'en', 'tool_policy': 'full_control',
                 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'e2e-key'}}}

        def resp(data, status=200):
            r = MagicMock(status_code=status, content=json.dumps(data).encode(), text=json.dumps(data))
            r.json.return_value = data
            r.raise_for_status = lambda: None
            return r

        async def mock_get(url, **kwargs):
            if '/api/tenant/devices' in url:
                return resp({'data': [
                    {'id': {'id': dev_a}, 'name': 'Simulated Tasmota A', 'type': 'default'},
                    {'id': {'id': dev_b}, 'name': 'Simulated Tasmota B', 'type': 'default'},
                ], 'hasNext': False})
            if f'/{dev_a}/values/attributes/CLIENT_SCOPE' in url:
                return resp([{'key': 'rpcCapabilities', 'value': tb_attributes[dev_a]['rpcCapabilities']}])
            if f'/{dev_b}/values/attributes/CLIENT_SCOPE' in url:
                return resp([{'key': 'rpcCapabilities', 'value': tb_attributes[dev_b]['rpcCapabilities']}])
            if '/values/attributes/' in url:
                return resp([])
            if f'/{dev_a}/values/timeseries' in url:
                return resp(tb_telemetry['Simulated Tasmota A'])
            if '/api/tenant/dashboards' in url or '/api/ruleChains' in url:
                return resp({'data': [], 'hasNext': False})
            return resp({})

        scripted_calls = []

        async def mock_post(url, **kwargs):
            if url.endswith('/v1/chat/completions'):
                msgs = kwargs['json']['messages']
                if msgs[-1]['role'] == 'user':
                    return resp({'choices': [{'message': {'role': 'assistant', 'tool_calls': [scripted_calls.pop(0)]}}]})
                return resp({'choices': [{'message': {'content': 'OK: ' + msgs[-1]['content']}}]})
            if '/api/rpc/twoway/' in url:
                target_id = url.rsplit('/', 1)[-1]
                dev_name = 'Simulated Tasmota A' if target_id == dev_a else 'Simulated Tasmota B'
                seq['id'] += 1
                req_id = seq['id']
                before = len(rpc_replies)
                body = kwargs['json']
                tasmota_bridge.request({'device': dev_name, 'data': {
                    'id': req_id, 'method': body['method'], 'params': body['params']}})
                self.assertEqual(len(rpc_replies), before + 1)
                return resp(rpc_replies[-1]['data'], 200)
            self.fail(f'Unexpected POST {url}')

        client = AsyncMock()
        client.get.side_effect = mock_get
        client.post.side_effect = mock_post
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {'PLATFORM_DATA_DIR': tmp}), \
                patch('httpx.AsyncClient', return_value=context):
            # 1. Discover devices
            scripted_calls.append({'id': 'c1', 'function': {'name': 'list_devices', 'arguments': '{}'}})
            r1 = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='What IoT devices are available?'))
            self.assertIn('Simulated Tasmota A', r1['tool_calls'][0]['result'])
            self.assertIn('Simulated Tasmota B', r1['tool_calls'][0]['result'])

            # 2. Discover capabilities for Simulated Tasmota A
            scripted_calls.append({'id': 'c2', 'function': {'name': 'list_available_rpc',
                'arguments': json.dumps({'device_identifier': 'Simulated Tasmota A'})}})
            r2 = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='What can Simulated Tasmota A do?'))
            caps_result = json.loads(r2['tool_calls'][0]['result'])
            self.assertEqual({c['method'] for c in caps_result['capabilities']}, {'setPower', 'getPower', 'togglePower'})

            # 3. Execute valid two-way RPC to turn on Simulated Tasmota A -> hits virtual Tasmota
            scripted_calls.append({'id': 'c3', 'function': {'name': 'execute_device_action',
                'arguments': json.dumps({'device_identifier': 'Simulated Tasmota A', 'method': 'setPower', 'params': {'state': True}})}})
            r3 = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='Turn on Simulated Tasmota A.'))
            rpc_ok = json.loads(r3['tool_calls'][0]['result'])
            self.assertTrue(rpc_ok['success'])
            self.assertTrue(rpc_ok['confirmed'])
            self.assertEqual(rpc_ok['delivery'], 'device_response')
            self.assertEqual(rpc_ok['response'], {'POWER': 'ON'})
            self.assertTrue(relay_power['relay-a'])

            # 4. Unsupported RPC on Simulated Tasmota B must be rejected before hitting bridge
            scripted_calls.append({'id': 'c4', 'function': {'name': 'execute_device_action',
                'arguments': json.dumps({'device_identifier': 'Simulated Tasmota B', 'method': 'togglePower', 'params': {}})}})
            r4 = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='Toggle Simulated Tasmota B.'))
            rpc_bad = json.loads(r4['tool_calls'][0]['result'])
            self.assertFalse(rpc_bad['success'])
            self.assertEqual(rpc_bad['error_code'], 'unsupported_method')
            self.assertFalse(relay_power['relay-b'])

            # 5. Query resulting state afterward
            scripted_calls.append({'id': 'c5', 'function': {'name': 'get_telemetry',
                'arguments': json.dumps({'device_name': 'Simulated Tasmota A'})}})
            r5 = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='What is its current state?'))
            state_res = json.loads(r5['tool_calls'][0]['result'])
            self.assertEqual(state_res['telemetry']['POWER'][0]['value'], 'ON')


if __name__ == '__main__':
    unittest.main()
