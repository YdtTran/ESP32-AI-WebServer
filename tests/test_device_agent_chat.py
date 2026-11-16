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
