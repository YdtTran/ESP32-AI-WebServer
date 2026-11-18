"""Authenticated native views of the configured ThingsBoard account."""
import hmac
import math
import os
import secrets
import time
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from .store import platform_store

router = APIRouter(prefix='/api/native-dashboards')


def valid_credentials(username, password, configured_username, configured_password):
    return bool(configured_username and configured_password and hmac.compare_digest(username, configured_username) and hmac.compare_digest(password, configured_password))


class Sessions:
    def __init__(self):
        self.tokens = {}

    def create(self, now=None):
        now = time.time() if now is None else now
        self.tokens = {token: expiry for token, expiry in self.tokens.items() if expiry > now}
        token = secrets.token_urlsafe(32)
        self.tokens[token] = now + 8 * 3600
        return token

    def valid(self, token, now=None):
        now = time.time() if now is None else now
        return bool(token and self.tokens.get(token, 0) > now)

    def revoke(self, token):
        self.tokens.pop(token, None)


def validate_settings(values):
    allowed = {'temperatureAlarmFlag': bool, 'humidityAlarmFlag': bool,
               'temperatureAlarmThreshold': float, 'humidityAlarmThreshold': float}
    if not isinstance(values, dict) or not values or set(values) - set(allowed):
        raise ValueError('Unsupported settings')
    for key, value in values.items():
        if allowed[key] is bool:
            if type(value) is not bool:
                raise ValueError('Expected a boolean')
        elif type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Expected a finite number')
    return values


sessions = Sessions()
login_attempts = {}


def require_session(authorization: str = Header(default='')):
    token = authorization.removeprefix('Bearer ')
    if not sessions.valid(token):
        raise HTTPException(401, 'Sign in to the console')
    return token


class Login(BaseModel):
    username: str
    password: str


@router.post('/login')
async def login(body: Login, request: Request):
    username = os.getenv('CONSOLE_USERNAME', '')
    password = os.getenv('CONSOLE_PASSWORD', '')
    if not username or not password:
        raise HTTPException(503, 'Console account is not configured')
    address = request.client.host if request.client else 'unknown'
    now = time.time()
    recent = [attempt for attempt in login_attempts.get(address, []) if attempt > now - 300]
    login_attempts[address] = recent
    if len(recent) >= 5:
        raise HTTPException(429, 'Too many sign-in attempts; try again later')
    if not valid_credentials(body.username, body.password, username, password):
        recent.append(now)
        raise HTTPException(401, 'Invalid credentials')
    login_attempts.pop(address, None)
    return {'token': sessions.create()}


@router.post('/logout')
async def logout(token: str = Depends(require_session)):
    sessions.revoke(token)
    return {'ok': True}


def connection():
    agent = platform_store.get_agent('agent_101') or {}
    tb = agent.get('integrations', {}).get('thingsboard', {})
    saved_host = (tb.get('host') or '').rstrip('/')
    host = (os.getenv('THINGSBOARD_HOST') or saved_host).rstrip('/')
    key = os.getenv('THINGSBOARD_API_KEY', '') or (tb.get('api_key') if host == saved_host else '')
    if not host or not key:
        raise HTTPException(503, 'ThingsBoard is not configured')
    return host, {'X-Authorization': f'ApiKey {key}'}


async def request_tb(client, method, url, **kwargs):
    try:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None
    except httpx.HTTPError as exc:
        raise HTTPException(502, 'ThingsBoard request failed') from exc


@router.get('')
async def dashboard_data(token: str = Depends(require_session)):
    host, headers = connection()
    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        dashboards = []
        devices = []
        for path, target in [('tenant/dashboards', dashboards), ('tenant/devices', devices)]:
            page = 0
            while True:
                result = await request_tb(client, 'GET', f'{host}/api/{path}', params={'pageSize': 100, 'page': page})
                target.extend(result.get('data', []))
                if not result.get('hasNext'):
                    break
                page += 1
        result_devices = []
        for device in devices:
            device_id = device['id']['id']
            telemetry = await request_tb(client, 'GET', f'{host}/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries')
            attributes = await request_tb(client, 'GET', f'{host}/api/plugins/telemetry/DEVICE/{device_id}/values/attributes/SERVER_SCOPE')
            result_devices.append({'id': device_id, 'name': device.get('name'), 'type': device.get('type'), 'telemetry': telemetry, 'attributes': {item['key']: item.get('value') for item in attributes}})
        return {'dashboards': [{'id': d['id']['id'], 'title': d.get('title')} for d in dashboards], 'devices': result_devices}


@router.post('/thermostats/{device_id}/settings')
async def update_settings(device_id: UUID, values: dict[str, Any], token: str = Depends(require_session)):
    try:
        validate_settings(values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    host, headers = connection()
    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        device = await request_tb(client, 'GET', f'{host}/api/device/{device_id}')
        if device.get('type', '').lower() != 'thermostat':
            raise HTTPException(400, 'Device is not a thermostat')
        await request_tb(client, 'POST', f'{host}/api/plugins/telemetry/DEVICE/{device_id}/SERVER_SCOPE', json=values)
    return {'ok': True}
