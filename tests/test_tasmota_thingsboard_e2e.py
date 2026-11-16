"""Opt-in live ThingsBoard, MQTT, simulator and MCP integration test.

Set TB_TEST_HOST, TB_TEST_USERNAME and TB_TEST_PASSWORD to a disposable tenant.
This test creates and deletes its own uniquely named devices and Compose project.
"""

import asyncio
import importlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'thingsboard-mcp'))


@unittest.skipUnless(all(os.getenv(key) for key in ('TB_TEST_HOST', 'TB_TEST_USERNAME', 'TB_TEST_PASSWORD')),
                     'requires a disposable ThingsBoard tenant')
class ThingsBoardTasmotaE2E(unittest.TestCase):
    def test_gateway_rpc_and_mcp_capability_execution(self):
        from src.capabilities import CapabilityRegistry
        from src.client import ThingsBoardClient
        from src.config import ThingsBoardConfig
        from src.tools.capabilities import register_capability_tools

        class Server:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def register(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return register

        suffix = secrets.token_hex(5)
        project = 'tasmota-test-' + suffix
        host = os.environ['TB_TEST_HOST'].rstrip('/')
        names = ['Test Tasmota A ' + suffix, 'Test Tasmota B ' + suffix]
        created_ids = []
        created_api_key = None
        created_chain = None
        env = os.environ.copy()
        env.update(MQTT_USERNAME='test-' + suffix, MQTT_PASSWORD=secrets.token_urlsafe(24),
                   TB_MQTT_HOST=os.getenv('TB_TEST_MQTT_HOST', 'host.docker.internal'),
                   TB_MQTT_PORT=os.getenv('TB_TEST_MQTT_PORT', '1883'))
        compose = ['docker', 'compose', '-p', project, '-f', str(ROOT / 'docker-compose.tasmota.yml')]
        http = httpx.Client(timeout=15)
        login = http.post(host + '/api/auth/login', json={'username': os.environ['TB_TEST_USERNAME'],
                                                         'password': os.environ['TB_TEST_PASSWORD']})
        login.raise_for_status()
        token = login.json()['token']
        headers = {'X-Authorization': 'Bearer ' + token}

        def call(method, path, **kwargs):
            response = http.request(method, host + path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None

        def wait_for_device(name):
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                response = http.get(host + '/api/tenant/devices', params={'deviceName': name}, headers=headers)
                if response.status_code == 200:
                    return response.json()
                time.sleep(.5)
            self.fail('Gateway did not register the simulated device')

        with tempfile.TemporaryDirectory() as temp:
            try:
                user = call('GET', '/api/auth/user')
                api_key = call('POST', '/api/apiKey', json={'userId': user['id'],
                    'description': 'Temporary Tasmota test ' + suffix, 'enabled': True})
                created_api_key = api_key['id']['id']
                gateway = call('POST', '/api/device', json={'name': 'Test Tasmota Gateway ' + suffix,
                                                            'type': 'default', 'additionalInfo': {'gateway': True}})
                gateway_id = gateway['id']['id']
                created_ids.append(gateway_id)
                credentials = call('GET', '/api/device/' + gateway_id + '/credentials')
                env['TB_GATEWAY_TOKEN'] = credentials['credentialsId']
                config = json.loads((ROOT / 'tools/tasmota-bridge.example.json').read_text())
                config['devices']['relay-a']['name'] = names[0]
                config['devices']['relay-b']['name'] = names[1]
                config_path = Path(temp) / 'devices.json'
                config_path.write_text(json.dumps(config))
                config_path.chmod(0o644)
                Path(temp).chmod(0o755)
                env['TASMOTA_BRIDGE_CONFIG'] = str(config_path)
                subprocess.run(compose + ['--profile', 'thingsboard', 'up', '-d', '--build'],
                               cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ids = []
                for name in names:
                    device_id = wait_for_device(name)['id']['id']
                    ids.append(device_id)
                    created_ids.append(device_id)
                chain = call('POST', '/api/ruleChain', json={'name': 'Test Tasmota rule ' + suffix,
                                                              'type': 'CORE', 'root': False})
                created_chain = chain['id']['id']
                call('POST', '/api/ruleChain/metadata', json={'ruleChainId': chain['id'], 'firstNodeIndex': 0,
                    'nodes': [
                        {'type': 'org.thingsboard.rule.engine.transform.TbChangeOriginatorNode', 'name': 'target',
                         'configuration': {'originatorSource': 'ENTITY', 'entityType': 'DEVICE',
                                           'entityNamePattern': names[0]}},
                        {'type': 'org.thingsboard.rule.engine.transform.TbTransformMsgNode', 'name': 'request',
                         'configuration': {'scriptLang': 'JS', 'jsScript':
                            'return {msg: {"method":"getPower", "params":{}}, metadata: metadata, msgType: msgType};'}},
                        {'type': 'org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode', 'name': 'rpc',
                         'configuration': {'timeoutInSeconds': 5}}],
                    'connections': [{'fromIndex': 0, 'toIndex': 1, 'type': 'Success'},
                                    {'fromIndex': 1, 'toIndex': 2, 'type': 'Success'}]})
                capability = {'methods': {
                    'setPower': {'description': 'Set relay power', 'params': {'type': 'object',
                        'properties': {'state': {'type': 'boolean'}}, 'required': ['state'], 'additionalProperties': False}},
                    'getPower': {'description': 'Read relay power', 'params': {'type': 'object',
                        'properties': {}, 'additionalProperties': False}},
                    'togglePower': {'description': 'Toggle relay power', 'params': {'type': 'object',
                        'properties': {}, 'additionalProperties': False}},
                }}
                call('POST', f'/api/plugins/telemetry/DEVICE/{ids[0]}/attributes/SERVER_SCOPE',
                     json={'rpcCapabilities': capability})
                capability['methods'].pop('togglePower')
                call('POST', f'/api/plugins/telemetry/DEVICE/{ids[1]}/attributes/SERVER_SCOPE',
                     json={'rpcCapabilities': capability})
                direct = call('POST', f'/api/rpc/twoway/{ids[0]}',
                              json={'method': 'setPower', 'params': {'state': True}, 'timeout': 10000})
                self.assertEqual(direct['POWER'], 'ON')
                call('POST', f'/api/rpc/oneway/{ids[0]}',
                     json={'method': 'setPower', 'params': {'state': False}})
                state = call('POST', f'/api/rpc/twoway/{ids[0]}',
                             json={'method': 'getPower', 'params': {}, 'timeout': 10000})
                self.assertEqual(state['POWER'], 'OFF')

                async def run_mcp():
                    async with httpx.AsyncClient(timeout=15) as concurrent_client:
                        async def send(state):
                            response = await concurrent_client.post(host + f'/api/rpc/twoway/{ids[0]}', headers=headers,
                                json={'method': 'setPower', 'params': {'state': state}, 'timeout': 10000})
                            response.raise_for_status()
                            return response.json()['POWER']
                        self.assertEqual(await asyncio.gather(send(True), send(False)), ['ON', 'OFF'])
                    client = ThingsBoardClient(ThingsBoardConfig(host=host, token=token))
                    registry = CapabilityRegistry(client, Path(temp) / 'capabilities.sqlite')
                    server = Server()
                    register_capability_tools(server, client, registry)
                    actions = await server.tools['list_available_rpc'](ids[0])
                    self.assertIn('setPower', [item['method'] for item in actions['capabilities']])
                    get_power = next(item for item in actions['capabilities'] if item['method'] == 'getPower')
                    self.assertTrue(any(source.startswith('rulechain:' + created_chain + ':node:')
                                        for source in get_power['sources']))
                    other_profile = ThingsBoardClient(ThingsBoardConfig(host=host, token=token,
                                                                         profile_id='different-agent'))
                    self.assertEqual(CapabilityRegistry(other_profile, Path(temp) / 'capabilities.sqlite').list(), [])
                    denied = await server.tools['execute_device_action'](ids[1], 'togglePower', {})
                    self.assertFalse(denied['success'])
                    result = await server.tools['execute_device_action'](ids[0], 'setPower', {'state': False})
                    self.assertTrue(result['success'], result)
                    self.assertEqual(result['response']['POWER'], 'OFF')
                    malformed = await server.tools['execute_device_action'](ids[0], 'setPower', {'state': 'yes'})
                    self.assertFalse(malformed['success'])

                    agent_tools = importlib.import_module('platform-bridge.agent_api_tools')
                    agent_config = {'host': host, 'api_key': api_key['value']}
                    with patch.dict(os.environ, {'PLATFORM_DATA_DIR': temp}):
                        async with httpx.AsyncClient() as agent_http:
                            denied = await agent_tools.execute(agent_http, agent_config, 'thingsboard_rpc',
                                {'device_id': ids[0], 'method': 'invented', 'params': {}}, profile_id='agent-a')
                            self.assertFalse(denied['success'])
                            malformed = await agent_tools.execute(agent_http, agent_config, 'thingsboard_rpc',
                                {'device_id': ids[0], 'method': 'setPower', 'params': {'state': 'yes'}}, profile_id='agent-a')
                            self.assertFalse(malformed['success'])
                            permitted = await agent_tools.execute(agent_http, agent_config, 'thingsboard_rpc',
                                {'device_id': ids[0], 'method': 'setPower', 'params': {'state': True}}, profile_id='agent-a')
                            self.assertTrue(permitted['success'], permitted)
                            self.assertEqual(permitted['data']['POWER'], 'ON')
                            call('PUT', f'/api/apiKey/{created_api_key}/enabled/false')
                            revoked = await agent_tools.execute(agent_http, agent_config, 'thingsboard_rpc',
                                {'device_id': ids[0], 'method': 'setPower', 'params': {'state': False}}, profile_id='agent-a')
                            self.assertFalse(revoked['success'])
                            observed = call('POST', f'/api/rpc/twoway/{ids[0]}',
                                json={'method': 'getPower', 'params': {}, 'timeout': 10000})
                            self.assertEqual(observed['POWER'], 'ON')

                    from mcp import ClientSession, StdioServerParameters
                    from mcp.client.stdio import stdio_client
                    profile = Path(temp) / 'agent.json'
                    profile.write_text(json.dumps({'id': 'test-agent-' + suffix, 'integrations': {
                        'thingsboard': {'host': host, 'token': token}}}))
                    profile.chmod(0o600)
                    server_env = {**os.environ, 'LOG_LEVEL': 'WARNING', 'PYTHONPATH': str(ROOT / 'thingsboard-mcp'),
                                  'TB_CAPABILITY_DB': str(Path(temp) / 'protocol.sqlite')}
                    server_params = StdioServerParameters(command=sys.executable,
                        args=['-m', 'src.server', '--profile', str(profile)], env=server_env)
                    async with stdio_client(server_params) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            response = await session.call_tool('execute_device_action', {
                                'device_identifier': ids[0], 'method': 'setPower', 'params': {'state': True}})
                            content = json.loads(response.content[0].text)
                            self.assertTrue(content['success'], content)
                            self.assertEqual(content['response']['POWER'], 'ON')
                asyncio.run(run_mcp())
                subprocess.run(compose + ['stop', 'tasmota-a'], cwd=ROOT, env=env, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
                offline = http.post(host + f'/api/rpc/twoway/{ids[0]}', headers=headers,
                                    json={'method': 'getPower', 'params': {}, 'timeout': 1500}, timeout=7)
                self.assertTrue(offline.status_code >= 400 or (offline.content and offline.json().get('error')),
                                f'Offline device unexpectedly returned HTTP {offline.status_code}')
            finally:
                subprocess.run(compose + ['--profile', 'thingsboard', 'down', '--remove-orphans', '--volumes'],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for name in names:
                    response = http.get(host + '/api/tenant/devices', params={'deviceName': name}, headers=headers)
                    if response.status_code == 200 and response.json()['id']['id'] not in created_ids:
                        created_ids.append(response.json()['id']['id'])
                for device_id in reversed(created_ids):
                    http.delete(host + '/api/device/' + device_id, headers=headers)
                if created_chain:
                    http.delete(host + '/api/ruleChain/' + created_chain, headers=headers)
                if created_api_key:
                    http.delete(host + '/api/apiKey/' + created_api_key, headers=headers)
                http.close()


if __name__ == '__main__':
    unittest.main()
