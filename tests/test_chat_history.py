import importlib
import unittest
from unittest.mock import patch

store_module = importlib.import_module('platform-bridge.store')


class ChatHistoryTests(unittest.TestCase):
    def test_device_reports_are_grouped_by_device_and_session(self):
        store = store_module.PlatformStore.__new__(store_module.PlatformStore)
        agent = {'id': 'test', 'chats': []}
        with patch.object(store, 'get_agent_for_device', return_value=agent), patch.object(store, 'save'), patch.dict('os.environ', {'MOCK_TIMESTAMP': '100'}):
            store.record_chat('aa:bb:cc:dd:ee:ff', 'one', 1, 'Hello')
            store.record_chat('aa:bb:cc:dd:ee:ff', 'one', 2, 'Hi')
            store.record_chat('11:22:33:44:55:66', 'one', 1, 'Other device')
        sessions = store.chat_sessions(agent)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[1]['messageCount'], 2)
        self.assertEqual([message['role'] for message in sessions[1]['messages']], ['user', 'assistant'])
        self.assertTrue(sessions[1]['createdAt'])
        self.assertEqual(agent['chats'][0]['timestamp'], 100)


if __name__ == '__main__':
    unittest.main()
