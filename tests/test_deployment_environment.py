"""Validate that separate installations receive their own connection settings."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeploymentEnvironmentTests(unittest.TestCase):
    def compose(self, filename, values):
        env = {k: v for k, v in os.environ.items() if k not in {
            'HOST_IP', 'API_KEY', 'BRIDGE_SECRET', 'THINGSBOARD_HOST',
            'THINGSBOARD_ALLOWED_ORIGINS', 'PUBLIC_WS_URL', 'PUBLIC_OTA_URL',
            'COREIOT_PUBLIC_ID', 'COREIOT_DASHBOARD_ID', 'COREIOT_URL',
        }}
        env.update(values)
        return subprocess.run(
            ['docker', 'compose', '--env-file', '/dev/null', '-f', str(ROOT / filename), 'config', '--format', 'json'],
            env=env, capture_output=True, text=True,
        )

    def test_machine_settings_reach_their_services(self):
        values = {'HOST_IP': '10.12.0.9', 'API_KEY': 'gateway-test-key',
                  'BRIDGE_SECRET': 'bridge-test-secret', 'THINGSBOARD_HOST': 'http://10.12.0.10:8080',
                  'THINGSBOARD_ALLOWED_ORIGINS': 'http://10.12.0.10:8080',
                  'COREIOT_URL': 'https://iot.example.test', 'COREIOT_PUBLIC_ID': 'tenant-specific-id',
                  'COREIOT_DASHBOARD_ID': 'dashboard-specific-id'}
        core = self.compose('docker-compose.yml', values)
        deploy = self.compose('docker-compose.deploy.yml', values)
        self.assertEqual(core.returncode, 0, core.stderr)
        self.assertEqual(deploy.returncode, 0, deploy.stderr)
        core_services = json.loads(core.stdout)['services']
        deploy_services = json.loads(deploy.stdout)['services']
        for name in ('gateway', 'mcp-gateway'):
            self.assertEqual(core_services[name]['environment']['API_KEY'], values['API_KEY'])
        coreiot = core_services['mcp-gateway']['environment']
        self.assertEqual(coreiot['COREIOT_PUBLIC_ID'], values['COREIOT_PUBLIC_ID'])
        self.assertEqual(coreiot['COREIOT_DASHBOARD_ID'], values['COREIOT_DASHBOARD_ID'])
        bridge = deploy_services['platform-bridge']['environment']
        self.assertEqual(bridge['API_KEY'], values['API_KEY'])
        self.assertEqual(bridge['PUBLIC_WS_URL'], 'ws://10.12.0.9:8005/xiaozhi/v1/')
        self.assertEqual(bridge['THINGSBOARD_HOST'], values['THINGSBOARD_HOST'])
        self.assertEqual(deploy_services['xiaozhi-server']['environment']['BRIDGE_SECRET'], values['BRIDGE_SECRET'])

    def test_standalone_simulator_requires_host_before_running_docker(self):
        env = {'PATH': os.environ['PATH']}
        result = subprocess.run(['bash', str(ROOT / 'start-tasmota-sim.sh')], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('TB_HOST', result.stderr)

    def test_missing_host_or_gateway_key_fails_closed(self):
        self.assertNotEqual(self.compose('docker-compose.yml', {}).returncode, 0)
        self.assertNotEqual(self.compose('docker-compose.deploy.yml', {'BRIDGE_SECRET': 'x', 'API_KEY': 'x'}).returncode, 0)


if __name__ == '__main__':
    unittest.main()
