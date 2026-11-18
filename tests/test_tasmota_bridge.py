import json
from pathlib import Path
import tempfile
import unittest

from tools.tasmota_bridge import TasmotaBridge, device_capabilities


class TasmotaBridgeTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.replies = []
        self.now = 10.0
        self.bridge = TasmotaBridge(
            {'relay-a': {'name': 'Relay A', 'methods': {
                'setPower': {'command': 'POWER', 'values': {'true': 'ON', 'false': 'OFF'}},
                'toggle': {'command': 'POWER', 'payload': 'TOGGLE'},
            }}},
            lambda topic, payload: self.sent.append((topic, payload)),
            lambda payload: self.replies.append(payload),
            lambda: self.now,
            timeout=5,
        )
        self.bridge.set_online('relay-a', True)

    def request(self, request_id, method='setPower', params=True):
        self.bridge.request({'device': 'Relay A', 'data': {'id': request_id, 'method': method, 'params': params}})

    def test_concurrent_requests_are_serialized_and_correlated(self):
        self.request(1)
        self.request(2, params=False)
        self.assertEqual(self.sent, [('cmnd/relay-a/POWER', 'ON')])
        self.bridge.result('relay-a', {'POWER': 'ON'})
        self.assertEqual(self.replies[0], {'device': 'Relay A', 'id': 1, 'data': {'POWER': 'ON'}})
        self.assertEqual(self.sent[-1], ('cmnd/relay-a/POWER', 'OFF'))
        self.bridge.result('relay-a', {'POWER': 'OFF'})
        self.assertEqual(self.replies[-1]['id'], 2)

    def test_timeout_blocks_late_response_from_being_used_by_next_request(self):
        self.request(1)
        self.request(2, 'toggle', {})
        self.now += 6
        self.bridge.expire()
        self.bridge.result('relay-a', {'POWER': 'ON'})
        self.assertEqual(len(self.sent), 1)
        self.assertEqual([r['id'] for r in self.replies], [1, 2])
        self.assertTrue(all('error' in r['data'] for r in self.replies))

    def test_duplicate_request_does_not_execute_twice(self):
        self.request(1, 'toggle', {})
        self.request(1, 'toggle', {})
        self.bridge.result('relay-a', {'POWER': 'ON'})
        self.request(1, 'toggle', {})
        self.assertEqual(len(self.sent), 1)

    def test_response_after_deadline_is_not_accepted_before_timeout_poll(self):
        self.request(1)
        self.request(2, 'toggle', {})
        self.now += 6
        self.bridge.result('relay-a', {'POWER': 'ON'})
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(all('error' in r['data'] for r in self.replies))

    def test_offline_and_invalid_parameters_are_rejected(self):
        self.bridge.request([])
        self.request(1, params='bad')
        self.request(3, method=[], params=True)
        self.bridge.set_online('relay-a', False)
        self.request(2)
        self.assertFalse(self.sent)
        self.assertTrue(all('error' in r['data'] for r in self.replies))

    def test_restart_quarantines_interrupted_command_and_rejects_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            db = directory + '/bridge.sqlite'
            first = TasmotaBridge(self.bridge.devices, lambda t, p: self.sent.append((t, p)),
                                  self.replies.append, lambda: self.now, journal=db)
            first.set_online('relay-a', True)
            first.request({'device': 'Relay A', 'data': {'id': 7, 'method': 'toggle', 'params': {}}})
            second = TasmotaBridge(self.bridge.devices, lambda t, p: self.sent.append((t, p)),
                                   self.replies.append, lambda: self.now, journal=db)
            second.set_online('relay-a', True)
            second.request({'device': 'Relay A', 'data': {'id': 7, 'method': 'toggle', 'params': {}}})
            second.request({'device': 'Relay A', 'data': {'id': 8, 'method': 'toggle', 'params': {}}})
            self.assertEqual(len(self.sent), 1)
            self.assertEqual([item['id'] for item in self.replies[-2:]], [7, 8])
            self.assertTrue(all('error' in item['data'] for item in self.replies[-2:]))

    def test_completed_request_id_remains_deduplicated_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            db = directory + '/bridge.sqlite'
            first = TasmotaBridge(self.bridge.devices, lambda t, p: self.sent.append((t, p)),
                                  self.replies.append, lambda: self.now, journal=db)
            first.set_online('relay-a', True)
            request = {'device': 'Relay A', 'data': {'id': 4, 'method': 'toggle', 'params': {}}}
            first.request(request)
            first.result('relay-a', {'POWER': 'ON'})
            second = TasmotaBridge(self.bridge.devices, lambda t, p: self.sent.append((t, p)),
                                   self.replies.append, lambda: self.now, journal=db)
            second.set_online('relay-a', True)
            second.request(request)
            self.assertEqual(len(self.sent), 1)
            self.assertIn('error', self.replies[-1]['data'])

    def test_automatic_rpc_capabilities_for_configured_devices(self):
        cfg = json.loads((Path(__file__).resolve().parents[1] / 'tools/tasmota-bridge.example.json').read_text())
        caps_a = device_capabilities(cfg['devices']['relay-a'])
        caps_b = device_capabilities(cfg['devices']['relay-b'])
        self.assertEqual(set(caps_a['methods']), {'setPower', 'getPower', 'togglePower'})
        self.assertEqual(set(caps_b['methods']), {'setPower', 'getPower'})
        self.assertEqual(caps_a['methods']['setPower']['params']['properties']['state']['type'], 'boolean')
        self.assertEqual(caps_a['methods']['getPower']['params']['properties'], {})

    def test_mismatched_power_result_is_not_correlated_with_pending_command(self):
        self.request(1, 'setPower', True)
        # Stale OFF result must not satisfy pending POWER ON command.
        self.bridge.result('relay-a', {'POWER': 'OFF'})
        self.assertEqual(self.replies, [])
        self.bridge.result('relay-a', {'POWER': 'ON'})
        self.assertEqual(self.replies[-1], {'device': 'Relay A', 'id': 1, 'data': {'POWER': 'ON'}})
        # Observed state is ON; toggling expects OFF and must ignore stale ON.
        self.request(2, 'toggle', {})
        self.bridge.result('relay-a', {'POWER': 'ON'})
        self.assertEqual(len(self.replies), 1)
        self.bridge.result('relay-a', {'POWER': 'OFF'})
        self.assertEqual(self.replies[-1], {'device': 'Relay A', 'id': 2, 'data': {'POWER': 'OFF'}})


if __name__ == '__main__':
    unittest.main()
