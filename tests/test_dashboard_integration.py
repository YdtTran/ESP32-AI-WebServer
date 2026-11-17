import importlib
import unittest

integration = importlib.import_module('platform-bridge.dashboard_integration')


class DashboardIntegrationTests(unittest.TestCase):
    def test_credentials_require_configured_account(self):
        self.assertFalse(integration.valid_credentials('admin', 'password', '', ''))
        self.assertTrue(integration.valid_credentials('operator', 'secret', 'operator', 'secret'))
        self.assertFalse(integration.valid_credentials('operator', 'wrong', 'operator', 'secret'))

    def test_only_expected_thermostat_attributes_are_accepted(self):
        self.assertEqual(integration.validate_settings({'temperatureAlarmFlag': True, 'temperatureAlarmThreshold': 28.5}),
                         {'temperatureAlarmFlag': True, 'temperatureAlarmThreshold': 28.5})
        for value in ({'unrelated': 1}, {'humidityAlarmFlag': 'true'}, {'humidityAlarmThreshold': float('nan')}, {}):
            with self.assertRaises(ValueError):
                integration.validate_settings(value)

    def test_session_expiration_and_revocation(self):
        sessions = integration.Sessions()
        token = sessions.create(now=100)
        self.assertTrue(sessions.valid(token, now=101))
        self.assertFalse(sessions.valid(token, now=100 + 8 * 3600))
        token = sessions.create(now=100)
        sessions.revoke(token)
        self.assertFalse(sessions.valid(token, now=101))

class DashboardEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        import httpx
        self.app = FastAPI()
        self.app.include_router(integration.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.token = integration.sessions.create()

    async def asyncTearDown(self):
        await self.client.aclose()
        integration.sessions.revoke(self.token)

    async def test_requires_session_and_rejects_invalid_settings(self):
        self.assertEqual((await self.client.get('/api/native-dashboards')).status_code, 401)
        response = await self.client.post('/api/native-dashboards/thermostats/00000000-0000-0000-0000-000000000001/settings', headers={'Authorization': 'Bearer ' + self.token}, json={'arbitrary': 1})
        self.assertEqual(response.status_code, 400)

    async def test_login_is_disabled_without_console_credentials(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'CONSOLE_USERNAME': '', 'CONSOLE_PASSWORD': ''}):
            response = await self.client.post('/api/native-dashboards/login', json={'username': 'operator', 'password': 'password'})
        self.assertEqual(response.status_code, 503)

    async def test_refuses_controls_for_other_device_types(self):
        from unittest.mock import patch, AsyncMock
        with patch.object(integration, 'connection', return_value=('https://example.test', {})), patch.object(integration, 'request_tb', new=AsyncMock(return_value={'type': 'sensor'})) as request:
            response = await self.client.post('/api/native-dashboards/thermostats/00000000-0000-0000-0000-000000000001/settings', headers={'Authorization': 'Bearer ' + self.token}, json={'humidityAlarmFlag': True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(request.await_count, 1)

    async def test_thermostat_write_is_limited_and_sent_to_thingsboard(self):
        from unittest.mock import patch, AsyncMock
        mocked = AsyncMock(side_effect=[{'type': 'thermostat'}, None])
        with patch.object(integration, 'connection', return_value=('https://example.test', {})), patch.object(integration, 'request_tb', new=mocked):
            response = await self.client.post('/api/native-dashboards/thermostats/00000000-0000-0000-0000-000000000001/settings', headers={'Authorization': 'Bearer ' + self.token}, json={'temperatureAlarmThreshold': 27})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.await_args.kwargs['json'], {'temperatureAlarmThreshold': 27})
        self.assertTrue(mocked.await_args.args[2].endswith('/SERVER_SCOPE'))

    async def test_read_returns_dashboard_and_current_device_data(self):
        from unittest.mock import patch, AsyncMock
        responses = [
            {'data': [{'id': {'id': 'dashboard-id'}, 'title': 'Thermostats'}], 'hasNext': False},
            {'data': [{'id': {'id': 'device-id'}, 'name': 'Room', 'type': 'thermostat'}], 'hasNext': False},
            {'temperature': [{'value': '23.5', 'ts': 100}]},
            [{'key': 'temperatureAlarmFlag', 'value': True}],
        ]
        with patch.object(integration, 'connection', return_value=('https://example.test', {})), patch.object(integration, 'request_tb', new=AsyncMock(side_effect=responses)):
            response = await self.client.get('/api/native-dashboards', headers={'Authorization': 'Bearer ' + self.token})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['dashboards'][0]['title'], 'Thermostats')
        self.assertEqual(data['devices'][0]['telemetry']['temperature'][0]['value'], '23.5')
        self.assertTrue(data['devices'][0]['attributes']['temperatureAlarmFlag'])


if __name__ == '__main__':
    unittest.main()
