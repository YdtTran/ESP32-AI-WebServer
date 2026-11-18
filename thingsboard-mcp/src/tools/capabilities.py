"""Capability discovery and validated ThingsBoard RPC tools."""

import asyncio
import logging
from typing import Any, Optional, Union

from ..capabilities import CapabilityError
from ..client import DeviceNotFoundError, ThingsBoardError

logger = logging.getLogger(__name__)


def register_capability_tools(mcp_server, client, registry):
    lock = asyncio.Lock()

    @mcp_server.tool()
    async def discover_capabilities() -> dict:
        """Refresh the registry from accessible ThingsBoard configuration, without sending RPC."""
        try:
            async with lock:
                items = await registry.refresh()
                return {'success': True, 'capabilities': items, 'discovery_gaps': registry.discovery_gaps}
        except (ThingsBoardError, ValueError) as exc:
            return {'success': False, 'error': str(exc)}

    @mcp_server.tool()
    async def list_available_rpc(device_identifier: str) -> dict:
        """List discovered RPC actions for an accessible device."""
        try:
            async with lock:
                await registry.refresh()
                device_id, name = await client.resolve_device(device_identifier)
                return {'success': True, 'device_id': device_id, 'device_name': name,
                        'capabilities': registry.list(device_id)}
        except (ThingsBoardError, ValueError) as exc:
            return {'success': False, 'error': str(exc)}

    @mcp_server.tool()
    async def get_platform_summary() -> dict:
        """Summarize accessible devices and their discovered actions."""
        try:
            async with lock:
                items = await registry.refresh()
                return {'success': True, 'capability_count': len(items), 'discovery_gaps': registry.discovery_gaps,
                        'devices': sorted(device.name for device in registry.devices),
                        'actions': [{'device': item['device_name'], 'method': item['method'],
                                     'schema_complete': item['schema_complete']} for item in items]}
        except (ThingsBoardError, ValueError) as exc:
            return {'success': False, 'error': str(exc)}

    @mcp_server.tool()
    async def execute_device_action(device_identifier: str, method: str,
                                    params: Optional[Union[dict, list, str, int, float, bool]] = None,
                                    timeout_ms: int = 5000, two_way: bool = True) -> dict:
        """Execute a currently advertised device action after validating its parameters."""
        if not 1 <= timeout_ms <= 120000:
            return {'success': False, 'confirmed': False, 'delivery': 'failed',
                    'error_code': 'invalid_params',
                    'error': 'Timeout must be between 1 and 120000 milliseconds.'}
        try:
            async with lock:
                await registry.refresh()
                device_id, _ = await client.resolve_device(device_identifier)
                value = {} if params is None else params
                registry.validate(device_id, method, value)
                result = await client.send_rpc(device_id, method, value, timeout_ms, two_way)
            data = result.model_dump()
            confirmed = bool(two_way and result.success and result.response not in (None, ''))
            data['confirmed'] = confirmed
            data['delivery'] = 'device_response' if confirmed else 'submitted' if result.success else 'failed'
            if not result.success:
                err_lower = (result.error or '').lower()
                is_offline = ('offline' in err_lower or 'disconnected' in err_lower
                              or ('timeout' in err_lower and any(
                                  item.get('availability') == 'offline' for item in registry.list(device_id))))
                data['error_code'] = (
                    'device_offline' if is_offline
                    else 'timeout' if ('timeout' in err_lower or 'timed out' in err_lower)
                    else 'device_not_found' if ('not found' in err_lower or 'does not exist' in err_lower)
                    else 'rpc_failed'
                )
            return data
        except (ThingsBoardError, CapabilityError, ValueError) as exc:
            msg = str(exc)
            code = (
                'device_not_found' if isinstance(exc, DeviceNotFoundError)
                else 'invalid_params' if (isinstance(exc, CapabilityError) and 'Invalid parameters' in msg)
                else 'unsupported_method' if isinstance(exc, CapabilityError)
                else 'error'
            )
            return {'success': False, 'confirmed': False, 'delivery': 'failed',
                    'error_code': code, 'error': msg}

    return execute_device_action
