import unittest

from tools.mcp_test_device import RPC_CONTRACT, reply


class SimulatorRpcTests(unittest.TestCase):
    def test_contract_describes_power_control(self):
        method = RPC_CONTRACT['methods']['setPower']
        self.assertEqual(method['params']['properties']['state']['type'], 'boolean')
        self.assertEqual(method['params']['required'], ['state'])

    def test_set_power_rejects_invalid_parameters_without_changing_state(self):
        state = {'power': False}
        for params in ({}, {'state': 'false'}, {'state': True, 'extra': 1}, True):
            self.assertIn('error', reply('setPower', params, state))
            self.assertIs(state['power'], False)
        self.assertIs(reply('setPower', {'state': True}, state)['power'], True)

    def test_no_argument_methods_reject_extra_parameters(self):
        state = {'power': False}
        for method in ('getState', 'togglePower'):
            self.assertIn('error', reply(method, {'unexpected': True}, state))
            self.assertIs(state['power'], False)

    def test_toggle_and_unknown_method(self):
        state = {'power': False}
        self.assertTrue(reply('togglePower', {}, state)['power'])
        self.assertFalse(reply('togglePower', {}, state)['power'])
        self.assertIn('error', reply('unknown', {}, state))
        self.assertFalse(state['power'])


if __name__ == '__main__':
    unittest.main()
