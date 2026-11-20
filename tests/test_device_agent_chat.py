import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

bridge = importlib.import_module('platform-bridge.app')


class DeviceAgentChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.allowed = patch.dict('os.environ', {'THINGSBOARD_ALLOWED_ORIGINS': 'http://tb.test'})
        self.allowed.start()
        self.addCleanup(self.allowed.stop)
        self.secret = patch.object(bridge, 'SECRET_KEY', 'test-service-secret')
        self.secret.start()
        self.addCleanup(self.secret.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_device_config_routes_to_its_agent(self):
        agent = {'id': 'test', 'name': 'Test', 'prompt': '', 'language': 'en'}
        with patch.object(bridge.device_db, 'get_device_by_mac', return_value={'agent_id': 'test'}), patch.object(bridge.accounts, 'get_agent_by_id', return_value=agent):
            result = await bridge.get_agent_models(bridge.AgentModelsRequest(macAddress='00:11:22:33:44:55'))
        self.assertTrue(result['data']['LLM']['OpenAILLM']['base_url'].endswith('/api/agents/test/v1'))

    async def test_openai_stream_uses_agent_and_requires_bridge_secret(self):
        payload = {'model': 'test', 'messages': [{'role': 'user', 'content': 'hello'}], 'stream': True}
        response = await self.client.post('/api/agents/agent-a/v1/chat/completions', json=payload)
        self.assertEqual(response.status_code, 401)
        with patch.object(bridge.accounts, 'get_agent_by_id', return_value={'id': 'agent-a'}), patch.object(bridge, 'run_agent_chat', new=AsyncMock(return_value={'reply': 'answer', 'tool_calls': []})) as chat:
            response = await self.client.post('/api/agents/agent-a/v1/chat/completions', json=payload, headers={'Authorization': 'Bearer ' + bridge.SECRET_KEY})
        self.assertEqual(response.status_code, 200)
        self.assertIn('answer', response.text)
        self.assertIn('[DONE]', response.text)
        self.assertEqual(chat.await_args.args[0]['id'], 'agent-a')
        self.assertEqual(chat.await_args.args[1].message, 'hello')

    async def test_device_chat_executes_rpc_with_agent_key(self):
        agent = {'id': 'agent-a', 'name': 'Test', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'agent-private'}}}
        rpc_id = '11111111-2222-3333-4444-555555555555'
        model_responses = [
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'one', 'function': {'name': 'thingsboard_get', 'arguments': '{"path":"/api/tenant/devices"}'}}]}}]},
            {'choices': [{'message': {'content': 'Tôi sẽ gọi hàm <tools></tools> để gửi yêu cầu RPC.'}}]},
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'two', 'function': {'name': 'thingsboard_rpc', 'arguments': '{"device_id":"' + rpc_id + '","method":"setPower","params":true,"two_way":false}'}}]}}]},
            {'choices': [{'message': {'content': 'The RPC succeeded.'}}]},
        ]
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        async def post(url, **kwargs):
            if url.startswith('http://tb.test'):
                self.assertEqual(kwargs['headers']['X-Authorization'], 'ApiKey agent-private')
                self.assertEqual(kwargs['json'], {'method': 'setPower', 'params': True})
                return response({}, 204)
            return response(model_responses.pop(0))
        fake = AsyncMock()
        fake.post.side_effect = post
        fake.get.return_value = response({'data': [{'name': 'switch', 'id': {'id': rpc_id}}]})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        request = MagicMock()
        request.json = AsyncMock(return_value={'model': 'test', 'messages': [{'role': 'user', 'content': 'Turn on the switch'}], 'stream': False})
        with patch.object(bridge.accounts, 'get_agent_by_id', return_value=agent), patch('httpx.AsyncClient', return_value=context), \
                patch.object(bridge.agent_api_tools, 'authorize_rpc', new=AsyncMock(return_value=None)) as authorize:
            result = await bridge.device_agent_chat('agent-a', request, authorization='Bearer ' + bridge.SECRET_KEY)
        self.assertEqual(result['choices'][0]['message']['content'], 'The RPC succeeded.')
        self.assertEqual(authorize.await_args.args[1:], ('agent-a', rpc_id, 'setPower', True))
        self.assertEqual(fake.get.await_count, 1)
        self.assertEqual(len(model_responses), 0)

    async def test_does_not_retry_a_described_rpc_after_one_was_sent(self):
        agent = {'id': 'agent-a', 'language': 'vi', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        device_id = '11111111-2222-3333-4444-555555555555'
        responses = [
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'rpc', 'function': {'name': 'thingsboard_rpc', 'arguments': '{"device_id":"' + device_id + '","method":"setPower","params":true}'}}]}}]},
            {'choices': [{'message': {'content': 'Tôi sẽ gửi yêu cầu RPC <tools></tools>.'}}]},
        ]
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        async def post(url, **kwargs):
            return response({}, 204) if url.startswith('http://tb.test') else response(responses.pop(0))
        fake = AsyncMock()
        fake.post.side_effect = post
        fake.get.return_value = response({'data': [{'name': 'switch', 'id': {'id': device_id}}]})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context), \
                patch.object(bridge.agent_api_tools, 'authorize_rpc', new=AsyncMock(return_value=None)):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='Turn on the switch'))
        self.assertEqual(len(result['tool_calls']), 1)
        self.assertIn('chưa thể xác nhận', result['reply'])
        self.assertEqual(fake.post.await_count, 3)

    async def test_retries_described_action_when_previous_rpc_was_not_sent(self):
        agent = {'id': 'agent-a', 'language': 'en', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        device_id = '11111111-2222-3333-4444-555555555555'
        responses = [
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'bad', 'function': {'name': 'execute_device_action', 'arguments': '{"device_identifier":"' + device_id + '","method":"POWER_ON"}'}}]}}]},
            {'choices': [{'message': {'content': 'The RPC method setPower is available. I will use it to turn on the device.'}}]},
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'good', 'function': {'name': 'execute_device_action', 'arguments': '{"device_identifier":"' + device_id + '","method":"setPower","params":{"state":true}}'}}]}}]},
            {'choices': [{'message': {'content': 'Power turned on.'}}]},
        ]
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        async def post(url, **kwargs):
            return response({'POWER': 'ON'}, 200) if url.startswith('http://tb.test') else response(responses.pop(0))
        fake = AsyncMock()
        fake.post.side_effect = post
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context), \
                patch.object(bridge.agent_api_tools, 'authorize_rpc', new=AsyncMock(side_effect=['RPC not sent: unsupported', None])):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='Turn on the switch'))
        self.assertEqual(result['reply'], 'Power turned on.')
        self.assertEqual(len(result['tool_calls']), 2)
        self.assertEqual(len(responses), 0)

    async def test_retries_hallucinated_rpc_claim_without_tool_call(self):
        agent = {'id': 'agent-a', 'language': 'en', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        device_id = '11111111-2222-3333-4444-555555555555'
        responses = [
            {'choices': [{'message': {'content': "The power has been set to OFF for the device."}}]},
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'real', 'function': {'name': 'execute_device_action', 'arguments': '{"device_identifier":"' + device_id + '","method":"setPower","params":{"state":false}}'}}]}}]},
            {'choices': [{'message': {'content': 'Power turned off.'}}]},
        ]
        seen_retry_roles = []
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        async def post(url, **kwargs):
            if url.startswith('http://tb.test'):
                return response({'POWER': 'OFF'}, 200)
            if len(responses) == 2:
                seen_retry_roles.extend(m['role'] for m in kwargs['json']['messages'][1:])
            return response(responses.pop(0))
        fake = AsyncMock()
        fake.post.side_effect = post
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context), \
                patch.object(bridge.agent_api_tools, 'authorize_rpc', new=AsyncMock(return_value=None)):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='Turn off the switch'))
        self.assertEqual(result['reply'], 'Power turned off.')
        self.assertEqual(len(result['tool_calls']), 1)
        self.assertNotIn('system', seen_retry_roles)
        self.assertEqual(len(responses), 0)

    async def test_retries_vietnamese_hallucinated_rpc_claim_without_tool_call(self):
        agent = {'id': 'agent-a', 'language': 'vi', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        device_id = '11111111-2222-3333-4444-555555555555'
        responses = [
            {'choices': [{'message': {'content': 'Xong rồi nhé.'}}]},
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'real', 'function': {'name': 'execute_device_action', 'arguments': '{"device_identifier":"' + device_id + '","method":"setPower","params":{"state":true}}'}}]}}]},
            {'choices': [{'message': {'content': 'Đã bật nguồn Simulated Tasmota B.'}}]},
        ]
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        async def post(url, **kwargs):
            return response({'POWER': 'ON'}, 200) if url.startswith('http://tb.test') else response(responses.pop(0))
        fake = AsyncMock()
        fake.post.side_effect = post
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context), \
                patch.object(bridge.agent_api_tools, 'authorize_rpc', new=AsyncMock(return_value=None)):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='Bật nguồn Simulated Tasmota B'))
        self.assertEqual(result['reply'], 'Đã bật nguồn Simulated Tasmota B.')
        self.assertEqual(len(result['tool_calls']), 1)
        self.assertEqual(len(responses), 0)

    async def test_does_not_repeat_rpc_when_reply_mentions_waiting_after_rpc_sent(self):
        agent = {'id': 'agent-a', 'language': 'en', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        device_id = '11111111-2222-3333-4444-555555555555'
        responses = [
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'rpc1', 'function': {'name': 'execute_device_action', 'arguments': '{"device_identifier":"' + device_id + '","method":"setPower","params":{"state":false}}'}}]}}]},
            {'choices': [{'message': {'content': 'Command submitted. Please wait a moment for the device to confirm.'}}]},
        ]
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        async def post(url, **kwargs):
            return response('', 200) if url.startswith('http://tb.test') else response(responses.pop(0))
        fake = AsyncMock()
        fake.post.side_effect = post
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context), \
                patch.object(bridge.agent_api_tools, 'authorize_rpc', new=AsyncMock(return_value=None)):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='tắt nguồn simulated tasmota A'))
        self.assertEqual(result['reply'], 'Command submitted. Please wait a moment for the device to confirm.')
        self.assertEqual(len(result['tool_calls']), 1)
        self.assertEqual(len(responses), 0)

    async def test_vietnamese_toggle_request_is_recognized_as_control_request(self):
        agent = {'id': 'agent-a', 'language': 'vi', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        device_id = '11111111-2222-3333-4444-555555555555'
        responses = [
            {'choices': [{'message': {'content': 'Vui lòng cung cấp UUID của thiết bị.'}}]},
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'tog', 'function': {'name': 'execute_device_action', 'arguments': '{"device_identifier":"' + device_id + '","method":"togglePower","params":{}}'}}]}}]},
            {'choices': [{'message': {'content': 'Đã đảo trạng thái nguồn.'}}]},
        ]
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        async def post(url, **kwargs):
            return response({'POWER': 'ON'}, 200) if url.startswith('http://tb.test') else response(responses.pop(0))
        fake = AsyncMock()
        fake.post.side_effect = post
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context), \
                patch.object(bridge.agent_api_tools, 'authorize_rpc', new=AsyncMock(return_value=None)):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='đảo trạng thái nguồn Simulated Tasmota A'))
        self.assertEqual(result['reply'], 'Đã đảo trạng thái nguồn.')
        self.assertEqual(len(result['tool_calls']), 1)
        self.assertEqual(len(responses), 0)

    async def test_retries_once_when_model_returns_empty_response(self):
        agent = {'id': 'agent-a', 'language': 'vi', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        responses = [
            {'choices': [{'message': {'content': ''}}]},
            {'choices': [{'message': {'content': 'Không tìm thấy thiết bị đèn phòng khách.'}}]},
        ]
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        fake = AsyncMock()
        fake.post.side_effect = [response(r) for r in responses]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='xin chào'))
        self.assertEqual(result['reply'], 'Không tìm thấy thiết bị đèn phòng khách.')
        self.assertEqual(fake.post.await_count, 2)

    async def test_does_not_force_rpc_when_model_asks_clarification_after_list_devices(self):
        agent = {'id': 'agent-a', 'language': 'en', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        responses = [
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'l1', 'function': {'name': 'list_devices', 'arguments': '{}'}}]}}]},
            {'choices': [{'message': {'content': 'Multiple devices match simulated tasmota. Which device do you want to turn on: Simulated Tasmota A or Simulated Tasmota B?'}}]},
        ]
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        fake = AsyncMock()
        fake.post.side_effect = [response(r) for r in responses]
        fake.get.return_value = response({'data': [{'name': 'Simulated Tasmota A', 'id': {'id': 'id-a'}}, {'name': 'Simulated Tasmota B', 'id': {'id': 'id-b'}}], 'hasNext': False})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='bật nguồn simulated tasmota'))
        self.assertIn('Which device', result['reply'])
        self.assertEqual(len(result['tool_calls']), 1)
        self.assertEqual(fake.post.await_count, 2)

    async def test_wraps_up_cleanly_when_tool_call_rounds_exhausted(self):
        agent = {'id': 'agent-a', 'language': 'en', 'integrations': {'thingsboard': {'host': 'http://tb.test', 'api_key': 'private'}}}
        def response(data, status=200):
            result = MagicMock(status_code=status)
            result.json.return_value = data
            return result
        blocked_call = {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'c', 'function': {'name': 'thingsboard_get', 'arguments': '{"path":"/api/device/123/credentials"}'}}]}}]}
        final_reply = {'choices': [{'message': {'content': 'Access to device credentials is not permitted.'}}]}
        fake = AsyncMock()
        fake.post.side_effect = [response(blocked_call)] * 6 + [response(final_reply)]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch('httpx.AsyncClient', return_value=context):
            result = await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='get device credentials'))
        self.assertEqual(result['reply'], 'Access to device credentials is not permitted.')
        self.assertEqual(len(result['tool_calls']), 6)
