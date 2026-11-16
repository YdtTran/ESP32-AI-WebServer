import unittest

from tools.virtual_tasmota import power_command


class VirtualTasmotaTests(unittest.TestCase):
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
