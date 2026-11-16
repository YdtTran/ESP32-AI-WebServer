import unittest

from tools.virtual_tasmota import power_command, mqtt_topic, timing_configuration


class FullTopicTests(unittest.TestCase):
    def test_custom_full_topic(self):
        self.assertEqual(mqtt_topic('site/%topic%/%prefix%/', 'relay', 'stat', 'POWER'),
                         'site/relay/stat/POWER')

    def test_invalid_full_topic(self):
        for template in ('%topic%/', '%prefix%/%topic%/+', '%prefix%/%topic%/#'):
            with self.subTest(template=template), self.assertRaises(ValueError):
                mqtt_topic(template, 'relay', 'cmnd', 'POWER')


class VirtualTasmotaTests(unittest.TestCase):
    def test_network_simulation_settings(self):
        self.assertEqual(timing_configuration({'TASMOTA_RESPONSE_DELAY': '0.5',
                                                'TASMOTA_DISCONNECT_AFTER': '10',
                                                'TASMOTA_DISCONNECT_DURATION': '2'}), (0.5, 10, 2))
        with self.assertRaises(ValueError):
            timing_configuration({'TASMOTA_RESPONSE_DELAY': '-1'})

    def test_power_commands_and_status_query(self):
        for payload, current, expected in [('', False, False), ('ON', False, True),
                                           ('0', True, False), ('toggle', True, False),
                                           ('true', False, True), ('?', True, True)]:
            self.assertEqual(power_command(payload, current), expected)

    def test_invalid_command_does_not_change_power(self):
        with self.assertRaises(ValueError):
            power_command('not-a-command', False)


if __name__ == '__main__':
    unittest.main()
