"""Read-only ThingsBoard capability discovery and isolated persistent storage."""

from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import re
import sqlite3

from jsonschema import Draft202012Validator, FormatChecker, exceptions


class CapabilityError(Exception):
    """A capability is absent, incomplete, or does not accept the parameters."""


def _widget_rpc_actions(value):
    if isinstance(value, dict):
        rpc = value.get('executeRpc')
        if value.get('action') == 'EXECUTE_RPC' and isinstance(rpc, dict):
            yield rpc.get('method')
        for child in value.values():
            yield from _widget_rpc_actions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _widget_rpc_actions(child)


def _literal_rule_rpc(script):
    if not isinstance(script, str):
        return None
    match = re.fullmatch(r'\s*return\s+\{\s*msg\s*:\s*(\{.*\})\s*,\s*metadata\s*:\s*metadata\s*,'
                         r'\s*msgType\s*:\s*msgType\s*\}\s*;?\s*', script, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None
    method = payload.get('method') if isinstance(payload, dict) and 'params' in payload else None
    return method if isinstance(method, str) and method.strip() else None


def _schema_complete(schema):
    if not isinstance(schema, dict):
        return False
    if '$schema' in schema and schema['$schema'] != 'https://json-schema.org/draft/2020-12/schema':
        return False
    # References can perform network access or depend on a schema we did not discover.
    def has_unsupported_constraints(value):
        if isinstance(value, dict):
            return any(k in ('$ref', '$dynamicRef', '$vocabulary') or
                       (k == '$schema' and v != 'https://json-schema.org/draft/2020-12/schema') or
                       (k == 'format' and (not isinstance(v, str) or v not in FormatChecker.checkers)) or
                       has_unsupported_constraints(v)
                       for k, v in value.items())
        return isinstance(value, list) and any(has_unsupported_constraints(v) for v in value)
    if has_unsupported_constraints(schema):
        return False
    try:
        Draft202012Validator.check_schema(schema)
    except exceptions.SchemaError:
        return False
    return True


class CapabilityRegistry:
    def __init__(self, client, database):
        self.client = client
        self.database = str(database)
        config = client.config
        identity = [config.host.rstrip('/'), config.api_key, config.username,
                    config.password, config.token, config.profile_id]
        self.integration_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        self.fresh = False
        self.devices = []
        self.discovery_gaps = []
        with closing(sqlite3.connect(self.database, timeout=15.0)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS capabilities (integration TEXT PRIMARY KEY, data TEXT NOT NULL)')

    def list(self, device_id=None):
        with closing(sqlite3.connect(self.database, timeout=15.0)) as db:
            row = db.execute('SELECT data FROM capabilities WHERE integration = ?', (self.integration_id,)).fetchone()
        capabilities = json.loads(row[0]) if row else []
        return [item for item in capabilities if device_id is None or item['device_id'] == device_id]

    async def refresh(self):
        self.fresh = False
        devices = []
        page = 0
        while True:
            result = await self.client.list_devices(page_size=100, page=page)
            devices.extend(result.devices)
            if not result.has_next:
                break
            page += 1
        discovered = []
        discovery_gaps = []
        availability = {}
        timestamp = datetime.now(timezone.utc).isoformat()

        def add_declaration(device, declaration, source):
            methods = declaration.get('methods') if isinstance(declaration, dict) else None
            if not isinstance(methods, dict):
                return
            for method, details in methods.items():
                if not isinstance(method, str) or not method.strip() or not isinstance(details, dict):
                    continue
                schema = details.get('params')
                equivalent = next((item for item in discovered if item['device_id'] == device.id
                                   and item['method'] == method and item['parameter_schema'] == schema), None)
                if equivalent:
                    if source not in equivalent['sources']:
                        equivalent['sources'].append(source)
                    continue
                discovered.append({'integration_id': self.integration_id, 'device_id': device.id,
                                   'device_name': device.name, 'device_type': device.type,
                                   'method': method, 'description': details.get('description', ''),
                                   'parameter_schema': schema, 'schema_complete': _schema_complete(schema),
                                   'supported_values': schema.get('enum') if isinstance(schema, dict) else None,
                                   'sources': [source], 'discovered_at': timestamp, 'available': True,
                                   'availability': 'unknown', 'authorization': {
                                       'device': 'accessible', 'rpc': 'checked_by_thingsboard_on_execution'}})

        def add_observation(device, method, references, description=''):
            device_id = device.id if device else None
            matches = [item for item in discovered if item['device_id'] == device_id and item['method'] == method]
            if not matches:
                if device:
                    add_declaration(device, {'methods': {method: {'description': description}}}, references[0])
                else:
                    discovered.append({'integration_id': self.integration_id, 'device_id': None,
                        'device_name': None, 'device_type': None, 'method': method, 'description': description,
                        'parameter_schema': None, 'schema_complete': False, 'supported_values': None,
                        'sources': [references[0]], 'discovered_at': timestamp, 'available': False,
                        'availability': 'unknown', 'authorization': {'device': 'unresolved', 'rpc': 'unknown'}})
                matches = [discovered[-1]]
            for item in matches:
                item['sources'].extend(ref for ref in references if ref not in item['sources'])

        for device in devices:
            scopes = await self.client.get_device_attributes(device.id)
            active = scopes.get('SERVER_SCOPE', {}).get('active')
            availability[device.id] = ('online' if active else 'offline') if isinstance(active, bool) else 'unknown'
            for scope, attributes in scopes.items():
                for key in ('rpcCapabilities', 'rpc_contract'):
                    add_declaration(device, attributes.get(key), f'device:{scope}:{key}')
        devices_by_id = {device.id: device for device in devices}
        for source, configuration in await self.client.get_configuration_sources():
            if source.startswith('rulechain:'):
                nodes = configuration.get('nodes') or []
                recognized_rpc_nodes = set()
                for node in nodes:
                    config = node.get('configuration') or {}
                    device = devices_by_id.get(config.get('deviceId'))
                    node_id = (node.get('id') or {}).get('id')
                    if device and node_id:
                        add_declaration(device, config.get('rpcCapabilities'), f'{source}:node:{node_id}')
                        if (node.get('type') == 'org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode'
                                and isinstance(config.get('rpcCapabilities'), dict)
                                and isinstance(config['rpcCapabilities'].get('methods'), dict)):
                            recognized_rpc_nodes.add(node_id)
                links = {(edge.get('fromIndex'), edge.get('toIndex')) for edge in
                         configuration.get('connections') or [] if edge.get('type') == 'Success'}
                for index, node in enumerate(nodes):
                    if node.get('type') != 'org.thingsboard.rule.engine.transform.TbTransformMsgNode':
                        continue
                    config = node.get('configuration') or {}
                    script_key = {'JS': 'jsScript', 'TBEL': 'tbelScript'}.get(config.get('scriptLang'))
                    method = _literal_rule_rpc(config.get(script_key)) if script_key else None
                    if not method:
                        continue
                    for origin_index, origin in enumerate(nodes):
                        origin_config = origin.get('configuration') or {}
                        name = origin_config.get('entityNamePattern')
                        if (origin.get('type') != 'org.thingsboard.rule.engine.transform.TbChangeOriginatorNode'
                                or (origin_index, index) not in links or origin_config.get('originatorSource') != 'ENTITY'
                                or origin_config.get('entityType') != 'DEVICE' or not isinstance(name, str)
                                or '${' in name or '$[' in name):
                            continue
                        targets = [device for device in devices if device.name == name]
                        if len(targets) != 1:
                            continue
                        for rpc_index, rpc in enumerate(nodes):
                            if ((index, rpc_index) not in links or
                                    rpc.get('type') != 'org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode'):
                                continue
                            ids = [(part.get('id') or {}).get('id') for part in (origin, node, rpc)]
                            if all(ids):
                                add_observation(targets[0], method, [f'{source}:node:{node_id}' for node_id in ids])
                                recognized_rpc_nodes.add(ids[-1])
                for node in nodes:
                    node_id = (node.get('id') or {}).get('id')
                    if (node.get('type') == 'org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode'
                            and node_id and node_id not in recognized_rpc_nodes):
                        discovery_gaps.append({'source': f'{source}:node:{node_id}',
                                               'reason': 'RPC method or target depends on runtime data'})
                continue
            if not source.startswith('dashboard:'):
                continue
            dashboard = configuration.get('configuration') or {}
            for widget_id, widget in (dashboard.get('widgets') or {}).items():
                if widget.get('type') != 'rpc':
                    continue
                config = widget.get('config') or {}
                target = config.get('targetDevice') or {}
                device_id = target.get('deviceId')
                state_dependent = False
                if target.get('type') == 'entity':
                    device_id = None
                    alias = (dashboard.get('entityAliases') or {}).get(target.get('entityAliasId')) or {}
                    entity_filter = alias.get('filter') or {}
                    state_dependent = entity_filter.get('type') == 'stateEntity' or bool(entity_filter.get('rootStateEntity'))
                    entity = entity_filter.get('singleEntity') or {}
                    if entity_filter.get('type') == 'singleEntity' and entity.get('entityType') == 'DEVICE':
                        device_id = entity.get('id')
                    elif entity_filter.get('type') == 'stateEntity':
                        default_entity = entity_filter.get('defaultStateEntity') or {}
                        if default_entity.get('entityType') == 'DEVICE':
                            device_id = default_entity.get('id')
                    elif (entity_filter.get('type') in ('entityList', 'entityName', 'entityType', 'deviceType',
                                                        'relationsQuery', 'deviceSearchQuery')
                          and not entity_filter.get('rootStateEntity')):
                        device_id = await self.client.resolve_alias_filter(entity_filter)
                device = devices_by_id.get(device_id)
                if not device and not state_dependent:
                    continue
                settings = config.get('settings') or {}
                methods = [settings.get(key) for key in ('methodName', 'setValueMethod')]
                if settings.get('retrieveValueMethod', 'rpc') == 'rpc':
                    methods.append(settings.get('getValueMethod'))
                if settings.get('performCheckStatus') is not False:
                    methods.append(settings.get('checkStatusMethod'))
                methods.extend((settings.get(key) or {}).get('method') for key in
                               ('gpioStatusRequest', 'gpioStatusChangeRequest')
                               if isinstance(settings.get(key), dict))
                methods.extend(_widget_rpc_actions(settings))
                methods.extend(_widget_rpc_actions(config.get('actions')))
                for method in methods:
                    if not isinstance(method, str) or not method.strip():
                        continue
                    reference = f'{source}:widget:{widget_id}'
                    add_observation(device, method, [reference], config.get('title', ''))
        for item in discovered:
            item['availability'] = availability.get(item['device_id'], 'unknown')
        with closing(sqlite3.connect(self.database, timeout=15.0)) as db, db:
            db.execute('INSERT OR REPLACE INTO capabilities VALUES (?, ?)',
                       (self.integration_id, json.dumps(discovered)))
        self.devices = devices
        self.discovery_gaps = discovery_gaps
        self.fresh = True
        return discovered

    def validate(self, device_id, method, params):
        if not self.fresh:
            raise CapabilityError('Capability discovery is not current.')
        matches = [item for item in self.list(device_id) if item['method'] == method]
        if len(matches) != 1:
            raise CapabilityError('Method is not uniquely advertised for this device.')
        item = matches[0]
        if not item['schema_complete']:
            raise CapabilityError('Parameter schema is incomplete.')
        try:
            Draft202012Validator(item['parameter_schema'], format_checker=FormatChecker()).validate(params)
        except exceptions.ValidationError as exc:
            raise CapabilityError(f'Invalid parameters: {exc.message}') from exc
        return item
