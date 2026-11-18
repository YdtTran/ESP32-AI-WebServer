"""Live timing test; run against a simulator configured for repeated disconnects."""

import os
import queue
import time
import unittest


@unittest.skipUnless(os.getenv('TASMOTA_TEST_TIMING'), 'requires configured MQTT timing simulation')
class TasmotaNetworkTests(unittest.TestCase):
    def test_delay_and_disconnect_reconnect(self):
        import paho.mqtt.client as mqtt

        messages = queue.Queue()
        connected = queue.Queue()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(os.environ['MQTT_USERNAME'], os.environ['MQTT_PASSWORD'])
        client.on_connect = lambda c, u, f, rc, p: (c.subscribe('stat/relay-a/RESULT'),
                                                    c.subscribe('tele/relay-a/LWT'), connected.put(rc))
        client.on_message = lambda c, u, m: messages.put((m.topic, m.payload.decode()))
        client.connect(os.environ['MQTT_HOST'])
        client.loop_start()
        try:
            self.assertFalse(connected.get(timeout=5).is_failure)
            deadline = time.monotonic() + 12
            statuses = []
            while time.monotonic() < deadline:
                topic, payload = messages.get(timeout=deadline - time.monotonic())
                if topic.endswith('/LWT'):
                    statuses.append(payload)
                    if statuses[-2:] == ['Offline', 'Online']:
                        break
            self.assertEqual(statuses[-2:], ['Offline', 'Online'])
            start = time.monotonic()
            client.publish('cmnd/relay-a/POWER', 'ON')
            while True:
                topic, payload = messages.get(timeout=5)
                if topic.endswith('/RESULT') and '"ON"' in payload:
                    break
            self.assertGreaterEqual(time.monotonic() - start, 0.2)
        finally:
            client.disconnect()
            client.loop_stop()


if __name__ == '__main__':
    unittest.main()
