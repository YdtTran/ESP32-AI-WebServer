import tempfile
import unittest
from unittest.mock import AsyncMock

from src.capabilities import CapabilityRegistry, CapabilityError
from src.config import ThingsBoardConfig
from src.schemas import DeviceSummary, ListDevicesResult
from src.tools.capabilities import register_capability_tools


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function
        return register


class CapabilityRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = AsyncMock()
        self.client.config = ThingsBoardConfig(host='https://tb.test', api_key='key-a')
        self.client.get_configuration_sources.return_value = []
        self.client.resolve_alias_filter.return_value = None
        self.client.list_devices.side_effect = [
            ListDevicesResult(total_devices=1, page=0, has_next=True, devices=[
                DeviceSummary(id='id-a', name='Relay A', type='relay')]),
            ListDevicesResult(total_devices=1, page=1, has_next=False, devices=[
                DeviceSummary(id='id-b', name='Relay B', type='relay')]),
        ]
        self.client.get_device_attributes.side_effect = [
            {'CLIENT_SCOPE': {'rpcCapabilities': {'methods': {'switch': {'description': 'Switch', 'params': {
                'type': 'boolean'}}}}}},
            {'CLIENT_SCOPE': {'rpcCapabilities': {'methods': {'inspect': {'description': 'Inspect'}}}}},
        ]
        self.registry = CapabilityRegistry(self.client, self.tmp.name + '/registry.sqlite')

    async def test_refresh_paginates_and_keeps_incomplete_schemas(self):
        result = await self.registry.refresh()
        self.assertEqual([(item['device_id'], item['method']) for item in result],
                         [('id-a', 'switch'), ('id-b', 'inspect')])
        self.assertTrue(result[0]['schema_complete'])
        self.assertFalse(result[1]['schema_complete'])
        self.assertEqual(result[0]['authorization']['rpc'], 'checked_by_thingsboard_on_execution')
        self.assertEqual(result[0]['availability'], 'unknown')
        self.assertEqual(self.registry.list('id-a')[0]['sources'][0], 'device:CLIENT_SCOPE:rpcCapabilities')
        self.client.send_rpc.assert_not_awaited()

    async def test_registry_isolated_and_validates_parameters(self):
        await self.registry.refresh()
        self.registry.validate('id-a', 'switch', False)
        for device, method, params in [('id-a', 'switch', 'false'), ('id-a', 'inspect', {}),
                                       ('id-b', 'inspect', {})]:
            with self.subTest(method=method), self.assertRaises(CapabilityError):
                self.registry.validate(device, method, params)
        self.client.config = ThingsBoardConfig(host='https://tb.test', api_key='key-b')
        other = CapabilityRegistry(self.client, self.tmp.name + '/registry.sqlite')
        self.assertEqual(other.list(), [])
        self.client.config = ThingsBoardConfig(host='https://tb.test', api_key='key-a', profile_id='agent-b')
        self.assertEqual(CapabilityRegistry(self.client, self.tmp.name + '/registry.sqlite').list(), [])

    async def test_schema_format_constraints_are_enforced(self):
        self.client.get_device_attributes.side_effect = [
            {'CLIENT_SCOPE': {'rpcCapabilities': {'methods': {'schedule': {'params': {
                'type': 'string', 'format': 'date-time'}}}}}}, {}]
        await self.registry.refresh()
        with self.assertRaises(CapabilityError):
            self.registry.validate('id-a', 'schedule', 'not-a-date')
        self.registry.validate('id-a', 'schedule', '2026-11-16T08:00:00Z')

    async def test_unknown_schema_format_is_not_treated_as_validated(self):
        self.client.get_device_attributes.side_effect = [
            {'CLIENT_SCOPE': {'rpcCapabilities': {'methods': {'schedule': {'params': {
                'type': 'string', 'format': 'custom-unknown-format'}}}}}}, {}]
        await self.registry.refresh()
        self.assertFalse(self.registry.list('id-a')[0]['schema_complete'])
        with self.assertRaises(CapabilityError):
            self.registry.validate('id-a', 'schedule', 'anything')

    async def test_unsupported_schema_dialect_is_incomplete(self):
        self.client.get_device_attributes.side_effect = [
            {'CLIENT_SCOPE': {'rpcCapabilities': {'methods': {'schedule': {'params': {
                '$schema': 'https://example.test/custom-schema', 'type': 'string'}}}}}}, {}]
        await self.registry.refresh()
        self.assertFalse(self.registry.list('id-a')[0]['schema_complete'])

    async def test_declared_supported_values_are_reported(self):
        self.client.get_device_attributes.side_effect = [
            {'CLIENT_SCOPE': {'rpcCapabilities': {'methods': {'mode': {'params': {
                'type': 'string', 'enum': ['auto', 'manual']}}}}}}, {}]
        await self.registry.refresh()
        self.assertEqual(self.registry.list('id-a')[0]['supported_values'], ['auto', 'manual'])

    async def test_failed_refresh_keeps_old_data_but_does_not_authorize(self):
        await self.registry.refresh()
        self.client.list_devices.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            await self.registry.refresh()
        self.assertEqual(len(self.registry.list()), 2)
        with self.assertRaises(CapabilityError):
            self.registry.validate('id-a', 'switch', True)

    async def test_execution_rechecks_discovery_and_rejects_unregistered_actions(self):
        mcp = FakeMcp()
        register_capability_tools(mcp, self.client, self.registry)
        self.client.resolve_device.return_value = ('id-a', 'Relay A')
        result = await mcp.tools['execute_device_action']('Relay A', 'invented', True)
        self.assertFalse(result['success'])
        self.client.send_rpc.assert_not_awaited()

    async def test_platform_summary_includes_devices_without_actions(self):
        self.client.get_device_attributes.side_effect = [{}, {}]
        server = FakeMcp()
        register_capability_tools(server, self.client, self.registry)
        summary = await server.tools['get_platform_summary']()
        self.assertEqual(summary['devices'], ['Relay A', 'Relay B'])
        self.assertEqual(summary['capability_count'], 0)

    async def test_equivalent_declarations_keep_both_sources(self):
        declaration = {'methods': {'switch': {'params': {'type': 'boolean'}}}}
        self.client.get_device_attributes.side_effect = [
            {'CLIENT_SCOPE': {'rpcCapabilities': declaration, 'rpc_contract': declaration}}, {}]
        capabilities = await self.registry.refresh()
        self.assertEqual(len(capabilities), 1)
        self.assertEqual(len(capabilities[0]['sources']), 2)

    async def test_shared_scope_declaration_is_discovered(self):
        self.client.get_device_attributes.side_effect = [
            {'SHARED_SCOPE': {'rpcCapabilities': {'methods': {'activate': {'params': {'type': 'boolean'}}}}}}, {}]
        items = await self.registry.refresh()
        self.assertEqual(items[0]['method'], 'activate')
        self.assertEqual(items[0]['sources'], ['device:SHARED_SCOPE:rpcCapabilities'])

    async def test_device_availability_uses_server_active_attribute(self):
        self.client.get_device_attributes.side_effect = [
            {'SERVER_SCOPE': {'active': False, 'rpcCapabilities': {'methods': {
                'switch': {'params': {'type': 'boolean'}}}}}}, {}]
        items = await self.registry.refresh()
        self.assertEqual(items[0]['availability'], 'offline')

    async def test_dashboard_widget_method_has_incomplete_schema(self):
        self.client.get_configuration_sources.return_value = [
            ('dashboard:dash-1', {'configuration': {'widgets': {'widget-1': {
                'type': 'rpc', 'config': {'targetDevice': {'type': 'device', 'deviceId': 'id-b'},
                                          'settings': {'methodName': 'reboot'}}}}}})]
        items = await self.registry.refresh()
        item = next(item for item in items if item['method'] == 'reboot')
        self.assertEqual(item['device_id'], 'id-b')
        self.assertFalse(item['schema_complete'])
        self.assertIn('dashboard:dash-1:widget:widget-1', item['sources'])

    async def test_dashboard_single_entity_alias_resolves_only_its_device(self):
        self.client.get_configuration_sources.return_value = [('dashboard:dash', {'configuration': {
            'entityAliases': {'alias': {'filter': {'type': 'singleEntity',
                'singleEntity': {'entityType': 'DEVICE', 'id': 'id-b'}}}},
            'widgets': {'widget': {'type': 'rpc', 'config': {'targetDevice': {
                'type': 'entity', 'entityAliasId': 'alias'}, 'settings': {'setValueMethod': 'adjust'}}}}}})]
        items = await self.registry.refresh()
        self.assertEqual([item['device_id'] for item in items if item['method'] == 'adjust'], ['id-b'])

    async def test_dashboard_dynamic_filter_uses_thingsboard_resolution(self):
        entity_filter = {'type': 'deviceType', 'deviceTypes': ['relay']}
        self.client.resolve_alias_filter.return_value = 'id-b'
        self.client.get_configuration_sources.return_value = [('dashboard:dash', {'configuration': {
            'entityAliases': {'alias': {'filter': entity_filter}}, 'widgets': {'widget': {'type': 'rpc',
                'config': {'targetDevice': {'type': 'entity', 'entityAliasId': 'alias'},
                           'settings': {'setValueMethod': 'adjust'}}}}}})]
        items = await self.registry.refresh()
        self.assertEqual([item['device_id'] for item in items if item['method'] == 'adjust'], ['id-b'])
        self.client.resolve_alias_filter.assert_awaited_once_with(entity_filter)

    async def test_dashboard_state_dependent_target_remains_unresolved(self):
        self.client.get_configuration_sources.return_value = [('dashboard:dash', {'configuration': {
            'entityAliases': {'alias': {'filter': {'type': 'stateEntity'}}}, 'widgets': {'widget': {'type': 'rpc',
                'config': {'targetDevice': {'type': 'entity', 'entityAliasId': 'alias'},
                           'settings': {'methodName': 'inspectState'}}}}}})]
        items = await self.registry.refresh()
        found = next(item for item in items if item['method'] == 'inspectState')
        self.assertIsNone(found['device_id'])
        self.assertFalse(found['schema_complete'])
        self.assertEqual(found['authorization']['device'], 'unresolved')
        self.assertNotIn(found, self.registry.list('id-a'))
        with self.assertRaises(CapabilityError):
            self.registry.validate('id-a', 'inspectState', {})

    async def test_dashboard_state_alias_uses_explicit_default_device(self):
        self.client.get_configuration_sources.return_value = [('dashboard:dash', {'configuration': {
            'entityAliases': {'alias': {'filter': {'type': 'stateEntity', 'defaultStateEntity': {
                'entityType': 'DEVICE', 'id': 'id-b'}}}}, 'widgets': {'widget': {'type': 'rpc',
                'config': {'targetDevice': {'type': 'entity', 'entityAliasId': 'alias'},
                           'settings': {'methodName': 'inspectState'}}}}}})]
        items = await self.registry.refresh()
        found = next(item for item in items if item['method'] == 'inspectState')
        self.assertEqual(found['device_id'], 'id-b')
        self.assertFalse(found['schema_complete'])

    async def test_gpio_widget_methods_are_discovered_without_inventing_schemas(self):
        self.client.get_configuration_sources.return_value = [('dashboard:dash', {'configuration': {
            'widgets': {'gpio': {'type': 'rpc', 'config': {'targetDevice': {'type': 'device', 'deviceId': 'id-b'},
                'settings': {'gpioStatusRequest': {'method': 'gpioRead'},
                             'gpioStatusChangeRequest': {'method': 'gpioWrite'}}}}}}})]
        items = await self.registry.refresh()
        methods = {item['method']: item for item in items if item['method'] in ('gpioRead', 'gpioWrite')}
        self.assertEqual(set(methods), {'gpioRead', 'gpioWrite'})
        self.assertTrue(all(not item['schema_complete'] for item in methods.values()))

    async def test_widget_modes_are_not_rpc_methods(self):
        self.client.get_configuration_sources.return_value = [('dashboard:dash', {'configuration': {
            'widgets': {'switch': {'type': 'rpc', 'config': {'targetDevice': {'deviceId': 'id-b'},
                'settings': {'retrieveValueMethod': 'attribute', 'getValueMethod': 'unusedRead',
                             'setValueMethod': 'write', 'performCheckStatus': False,
                             'checkStatusMethod': 'unusedStatus'}}}}}})]
        methods = [item['method'] for item in await self.registry.refresh()]
        self.assertIn('write', methods)
        self.assertFalse({'attribute', 'unusedRead', 'unusedStatus'} & set(methods))

    async def test_widget_action_settings_include_only_enabled_rpc_methods(self):
        self.client.get_configuration_sources.return_value = [('dashboard:dash', {'configuration': {
            'widgets': {'power': {'type': 'rpc', 'config': {'targetDevice': {'deviceId': 'id-b'},
                'settings': {'onUpdateState': {'action': 'EXECUTE_RPC', 'executeRpc': {'method': 'turnOn'}},
                             'offUpdateState': {'action': 'DO_NOTHING', 'executeRpc': {'method': 'inactive'}}}}}}}})]
        methods = [item['method'] for item in await self.registry.refresh()]
        self.assertIn('turnOn', methods)
        self.assertNotIn('inactive', methods)

    async def test_rule_chain_explicit_declaration_and_inaccessible_target(self):
        self.client.get_configuration_sources.return_value = [('rulechain:chain', {'nodes': [
            {'id': {'id': 'node'}, 'configuration': {'deviceId': 'id-a', 'rpcCapabilities': {
                'methods': {'calibrate': {'description': 'Calibrate', 'params': {'type': 'integer'}}}}}},
            {'id': {'id': 'hidden'}, 'configuration': {'deviceId': 'id-hidden', 'rpcCapabilities': {
                'methods': {'reset': {'params': {'type': 'object'}}}}}},
        ]})]
        items = await self.registry.refresh()
        found = [item for item in items if item['method'] == 'calibrate']
        self.assertEqual(found[0]['device_id'], 'id-a')
        self.assertEqual(found[0]['sources'], ['rulechain:chain:node:node'])
        self.assertNotIn('reset', [item['method'] for item in items])
        self.registry.validate('id-a', 'calibrate', 2)

    async def test_rule_chain_literal_request_with_fixed_originator(self):
        self.client.get_configuration_sources.return_value = [('rulechain:chain', {
            'nodes': [
                {'id': {'id': 'target'}, 'type': 'org.thingsboard.rule.engine.transform.TbChangeOriginatorNode',
                 'configuration': {'originatorSource': 'ENTITY', 'entityType': 'DEVICE', 'entityNamePattern': 'Relay B'}},
                {'id': {'id': 'transform'}, 'type': 'org.thingsboard.rule.engine.transform.TbTransformMsgNode',
                 'configuration': {'scriptLang': 'JS', 'jsScript':
                     'return {msg: {"method":"restart", "params":{}}, metadata: metadata, msgType: msgType};'}},
                {'id': {'id': 'rpc'}, 'type': 'org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode',
                 'configuration': {}}],
            'connections': [{'fromIndex': 0, 'toIndex': 1, 'type': 'Success'},
                            {'fromIndex': 1, 'toIndex': 2, 'type': 'Success'}]})]
        items = await self.registry.refresh()
        found = next(item for item in items if item['method'] == 'restart')
        self.assertEqual(found['device_id'], 'id-b')
        self.assertFalse(found['schema_complete'])
        self.assertEqual(set(found['sources']), {'rulechain:chain:node:target',
                         'rulechain:chain:node:transform', 'rulechain:chain:node:rpc'})

    async def test_rule_chain_dynamic_script_does_not_invent_a_method(self):
        self.client.get_configuration_sources.return_value = [('rulechain:chain', {
            'nodes': [
                {'id': {'id': 'target'}, 'type': 'org.thingsboard.rule.engine.transform.TbChangeOriginatorNode',
                 'configuration': {'originatorSource': 'ENTITY', 'entityType': 'DEVICE', 'entityNamePattern': '${name}'}},
                {'id': {'id': 'transform'}, 'type': 'org.thingsboard.rule.engine.transform.TbTransformMsgNode',
                 'configuration': {'scriptLang': 'JS', 'jsScript':
                     'return {msg: {"method":"restart", "params":{}}, metadata: metadata, msgType: msgType};'}},
                {'id': {'id': 'rpc'}, 'type': 'org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode',
                 'configuration': {}}],
            'connections': [{'fromIndex': 0, 'toIndex': 1, 'type': 'Success'},
                            {'fromIndex': 1, 'toIndex': 2, 'type': 'Success'}]})]
        self.assertNotIn('restart', [item['method'] for item in await self.registry.refresh()])
        self.assertEqual(self.registry.discovery_gaps[0]['source'], 'rulechain:chain:node:rpc')

    async def test_rule_chain_observation_keeps_declared_schema(self):
        self.client.get_configuration_sources.return_value = [('rulechain:chain', {'nodes': [
            {'id': {'id': 'target'}, 'type': 'org.thingsboard.rule.engine.transform.TbChangeOriginatorNode',
             'configuration': {'originatorSource': 'ENTITY', 'entityType': 'DEVICE', 'entityNamePattern': 'Relay A'}},
            {'id': {'id': 'transform'}, 'type': 'org.thingsboard.rule.engine.transform.TbTransformMsgNode',
             'configuration': {'scriptLang': 'TBEL', 'tbelScript':
                 'return {msg: {"method":"switch", "params":true}, metadata: metadata, msgType: msgType};'}},
            {'id': {'id': 'rpc'}, 'type': 'org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode', 'configuration': {}}],
            'connections': [{'fromIndex': 0, 'toIndex': 1, 'type': 'Success'},
                            {'fromIndex': 1, 'toIndex': 2, 'type': 'Success'}]})]
        await self.registry.refresh()
        matches = [item for item in self.registry.list('id-a') if item['method'] == 'switch']
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]['schema_complete'])
        self.assertIn('rulechain:chain:node:rpc', matches[0]['sources'])
        self.registry.validate('id-a', 'switch', True)

    async def test_refresh_removes_deleted_devices_and_revoked_capabilities(self):
        await self.registry.refresh()
        self.client.list_devices.side_effect = None
        self.client.list_devices.return_value = ListDevicesResult(total_devices=1, page=0, has_next=False,
            devices=[DeviceSummary(id='id-a', name='Relay A', type='relay')])
        self.client.get_device_attributes.side_effect = None
        self.client.get_device_attributes.return_value = {}
        self.assertEqual(await self.registry.refresh(), [])
        self.assertEqual(self.registry.list(), [])
        with self.assertRaises(CapabilityError):
            self.registry.validate('id-a', 'switch', True)

    async def test_widget_config_actions_and_explicit_rpc_node_declarations(self):
        self.client.get_configuration_sources.return_value = [
            ('dashboard:dash', {'configuration': {'widgets': {'btn': {'type': 'rpc', 'config': {
                'targetDevice': {'deviceId': 'id-b'},
                'actions': {'headerButton': [{'action': 'EXECUTE_RPC', 'executeRpc': {'method': 'pulse'}},
                                             {'action': 'DO_NOTHING', 'executeRpc': {'method': 'ignored'}}]}}}}}}),
            ('rulechain:chain', {'nodes': [
                {'id': {'id': 'rpc-explicit'}, 'type': 'org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode',
                 'configuration': {'deviceId': 'id-a', 'rpcCapabilities': {
                     'methods': {'calibrate': {'params': {'type': 'integer'}}}}}}]}),
        ]
        items = await self.registry.refresh()
        self.assertIn('pulse', [item['method'] for item in items if item['device_id'] == 'id-b'])
        self.assertNotIn('ignored', [item['method'] for item in items])
        self.assertEqual(self.registry.discovery_gaps, [])
        self.registry.validate('id-a', 'calibrate', 5)

    async def test_conflicting_schemas_across_sources_are_rejected(self):
        self.client.get_device_attributes.side_effect = [
            {'CLIENT_SCOPE': {'rpcCapabilities': {'methods': {'switch': {'params': {'type': 'boolean'}}}}},
             'SERVER_SCOPE': {'rpcCapabilities': {'methods': {'switch': {'params': {'type': 'integer'}}}}}},
            {},
        ]
        await self.registry.refresh()
        with self.assertRaises(CapabilityError):
            self.registry.validate('id-a', 'switch', True)
