import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

bridge = importlib.import_module('platform-bridge.app')


class ResponseLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_model_uses_gateway_key_not_bridge_secret(self):
        with patch.dict('os.environ', {'API_KEY': 'gateway-secret'}), \
             patch.object(bridge, 'SECRET_KEY', 'bridge-secret'):
            config = await bridge.get_server_base_config()
        self.assertEqual(config['data']['LLM']['OpenAILLM']['api_key'], 'gateway-secret')

    async def test_device_prompt_uses_selected_language(self):
        agent = {'id': 'test', 'name': 'Test', 'language': 'en', 'prompt': 'Reply in Vietnamese.'}
        with patch.object(bridge.device_db, 'get_device_by_mac', return_value={'agent_id': 'test'}), patch.object(bridge.accounts, 'get_agent_by_id', return_value=agent):
            result = await bridge.get_agent_models(bridge.AgentModelsRequest(macAddress='00:11:22:33:44:55'))
        prompt = result['data']['prompt']
        self.assertIn('Reply in Vietnamese.', prompt)
        self.assertIn('English', prompt)
        self.assertIn('takes precedence', prompt)

    async def test_console_prompt_uses_selected_language(self):
        agent = {'id': 'test', 'name': 'Test', 'language': 'vi', 'prompt': 'Reply in English.'}
        response = MagicMock()
        response.json.return_value = {'choices': [{'message': {'content': 'Xin chào'}}]}
        client = AsyncMock()
        client.post.return_value = response
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(bridge.platform_store, 'get_agent', return_value=agent), patch.object(bridge.platform_store, 'sync_to_agent_profile_json'), patch('httpx.AsyncClient', return_value=context):
            await bridge.run_agent_chat(agent, bridge.AgentChatRequest(message='Hello'))
        prompt = client.post.call_args.kwargs['json']['messages'][0]['content']
        self.assertIn('Reply in English.', prompt)
        self.assertIn('Vietnamese', prompt)
        self.assertIn('takes precedence', prompt)


if __name__ == '__main__':
    unittest.main()
