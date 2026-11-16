"""Live tests for the Compose simulator; run with MQTT_HOST and credentials set."""

import json
import os
import queue
import unittest


@unittest.skipUnless(os.getenv('MQTT_HOST'), 'requires a running MQTT simulator')
class TasmotaMqttTests(unittest.TestCase):
    def test_independent_relays_and_telemetry(self):
        import paho.mqtt.client as mqtt

        messages = queue.Queue()
        availability = queue.Queue()
        connected = queue.Queue()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(os.environ['MQTT_USERNAME'], os.environ['MQTT_PASSWORD'])
        client.on_connect = lambda c, u, f, rc, p: (c.subscribe('stat/+/RESULT'), c.subscribe('tele/+/+'), connected.put(rc))
        def on_message(client, userdata, message):
            item = (message.topic, message.payload.decode())
            (availability if message.topic.endswith('/LWT') else messages).put(item)
        client.on_message = on_message
        client.connect(os.environ['MQTT_HOST'], int(os.getenv('MQTT_PORT', '1883')))
        client.loop_start()
        try:
            self.assertFalse(connected.get(timeout=5).is_failure)

            def command(device, payload, expected):
                client.publish(f'cmnd/{device}/POWER', payload, qos=1).wait_for_publish(timeout=5)
                while True:
                    topic, value = messages.get(timeout=5)
                    if topic == f'stat/{device}/RESULT' and json.loads(value).get('POWER') == expected:
                        return

            command('relay-a', 'ON', 'ON')
            command('relay-b', 'OFF', 'OFF')
            command('relay-a', '?', 'ON')
            command('relay-a', 'TOGGLE', 'OFF')
            self.assertEqual({availability.get(timeout=5), availability.get(timeout=5)},
                             {('tele/relay-a/LWT', 'Online'), ('tele/relay-b/LWT', 'Online')})
        finally:
            client.disconnect()
            client.loop_stop()


if __name__ == '__main__':
    unittest.main()
