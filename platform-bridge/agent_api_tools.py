"""Per-agent ThingsBoard reads and RPC using the agent's configured API key."""

import re
import importlib
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit


def redact_credentials(value, credential):
    if isinstance(value, dict):
        return {key: ('[redacted]' if re.search(r'password|credential|secret|api.?key|access.?token|refresh.?token', str(key), re.IGNORECASE) else redact_credentials(item, credential))
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact_credentials(item, credential) for item in value]
    if isinstance(value, str) and credential:
        return value.replace(credential, "[redacted]")
    return value


async def authorize_rpc(config, profile_id, device_id, method, params):
    """Refresh and validate against the same registry used by the MCP server."""
    if not profile_id:
        return 'An agent profile is required for RPC authorization.'
    try:
        capabilities = importlib.import_module('thingsboard-mcp.src.capabilities')
        tb_client = importlib.import_module('thingsboard-mcp.src.client')
        tb_config = importlib.import_module('thingsboard-mcp.src.config')
        settings = tb_config.ThingsBoardConfig(host=config['host'], api_key=config['api_key'], profile_id=profile_id)
        connection = tb_client.ThingsBoardClient(settings)
        data_dir = Path(os.getenv('PLATFORM_DATA_DIR', '/app/data' if os.path.exists('/app') else './data'))
        data_dir.mkdir(parents=True, exist_ok=True)
        registry = capabilities.CapabilityRegistry(connection, data_dir / 'capabilities.sqlite3')
        await registry.refresh()
        resolved_id, _ = await connection.resolve_device(device_id)
        registry.validate(resolved_id, method, params)
        return None
    except Exception:
        return 'RPC not sent: capability discovery or parameter validation failed.'


async def execute(client, config, name, args, read_only=False, profile_id=None):
    host = (config.get('host') or '').rstrip('/')
    key = config.get('api_key') or ''
    if not host or not key:
        return {'success': False, 'error': 'ThingsBoard host or API key is not configured.'}
    headers = {'X-Authorization': f'ApiKey {key}'}
    try:
        if name == 'thingsboard_get':
            path = args.get('path', '')
            parsed = urlsplit(path)
            decoded = unquote(parsed.path)
            if (not path.startswith('/api/') or parsed.scheme or parsed.netloc or parsed.fragment
                    or any(part in ('.', '..') for part in decoded.split('/'))
                    or '\\' in decoded or '%' in decoded or any(ord(c) < 32 for c in decoded)
                    or re.search(r'credential|password|secret|api.?key|token', decoded, re.IGNORECASE)):
                return {'success': False, 'error': 'Invalid ThingsBoard API path.'}
            response = await client.get(host + path, params=args.get('query'), headers=headers)
        elif name == 'thingsboard_query':
            path = args.get('path', '')
            if path not in ('/api/entitiesQuery/find', '/api/entitiesQuery/count',
                            '/api/alarmsQuery/find', '/api/alarmsQuery/count', '/api/v2/entitiesQuery/find/keys') or not isinstance(args.get('body'), dict):
                return {'success': False, 'error': 'Invalid read-only ThingsBoard query.'}
            response = await client.post(host + path, json=args['body'], headers=headers)
        elif name == 'thingsboard_rpc':
            if read_only:
                return {'success': False, 'error': 'RPC is disabled by this agent policy.'}
            device_id = args.get('device_id', '')
            method = args.get('method', '')
            if not re.fullmatch(r'[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', device_id) or not isinstance(method, str) or not method.strip():
                return {'success': False, 'error': 'A device UUID and RPC method are required.'}
            two_way = args.get('two_way', True)
            if not isinstance(two_way, bool):
                return {'success': False, 'error': 'RPC delivery mode must be boolean.'}
            mode = 'twoway' if two_way else 'oneway'
            timeout_ms = args.get('timeout_ms', 5000)
            if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1 <= timeout_ms <= 120000:
                return {'success': False, 'error': 'RPC timeout must be between 1 and 120000 milliseconds.'}
            payload = {'method': method, 'params': args.get('params', {})}
            error = await authorize_rpc(config, profile_id, device_id, method, payload['params'])
            if error:
                return {'success': False, 'error': error}
            if mode == 'twoway':
                payload['timeout'] = timeout_ms
            response = await client.post(host + f'/api/rpc/{mode}/{device_id}', headers=headers,
                                         json=payload, timeout=timeout_ms / 1000 + 5)
        else:
            return {'success': False, 'error': 'Unknown tool.'}
        if not 200 <= response.status_code < 300:
            return {'success': False, 'status': response.status_code, 'error': 'ThingsBoard request failed.'}
        try:
            data = response.json()
        except ValueError:
            data = response.text
        if name == 'thingsboard_rpc':
            data = redact_credentials(data, key)
            if mode == 'twoway' and isinstance(data, dict) and data.get('error'):
                return {'success': False, 'error': 'Device RPC failed.', 'data': data}
            return {'success': True, 'data': data, 'delivery': 'device_response' if mode == 'twoway' else 'submitted'}
        return {'success': True, 'data': redact_credentials(data, key)}
    except Exception:
        return {'success': False, 'error': 'Could not contact ThingsBoard.'}


TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'thingsboard_get',
            'description': 'Read data from the configured ThingsBoard REST API. Use the API key permissions. Examples: /api/tenant/devices?pageSize=100&page=0 to find device IDs, /api/plugins/telemetry/DEVICE/{deviceId}/values/timeseries for latest telemetry, and /api/plugins/telemetry/DEVICE/{deviceId}/values/attributes for attributes. Follow pagination when hasNext is true.',
            'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'A relative ThingsBoard /api/ path including any query string.'}}, 'required': ['path']},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'thingsboard_query',
            'description': 'Search ThingsBoard entities or alarms using a read-only query. Supported paths: /api/entitiesQuery/find, /api/entitiesQuery/count, /api/alarmsQuery/find, /api/alarmsQuery/count, /api/v2/entitiesQuery/find/keys.',
            'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'body': {'type': 'object', 'description': 'ThingsBoard entity or alarm query object.'}}, 'required': ['path', 'body']},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'thingsboard_rpc',
            'description': 'Send a registered RPC method to a ThingsBoard device after capability and parameter validation. Use one-way RPC when the device does not send a reply.',
            'parameters': {'type': 'object', 'properties': {'device_id': {'type': 'string', 'description': 'The exact ThingsBoard device UUID.'}, 'method': {'type': 'string'}, 'params': {'description': 'Any JSON RPC parameters.'}, 'two_way': {'type': 'boolean', 'description': 'Wait for the device response when true.'}, 'timeout_ms': {'type': 'integer', 'description': 'Timeout in milliseconds for a two-way RPC (default 5000, maximum 120000).'}}, 'required': ['device_id', 'method']},
        },
    },
]
