import importlib
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

bridge = importlib.import_module('platform-bridge.app')


class DeviceWebSocketTests(unittest.TestCase):
    def test_missing_device_token_is_rejected(self):
        with TestClient(bridge.app) as client:
            with self.assertRaises(WebSocketDisconnect) as error:
                with client.websocket_connect('/xiaozhi/v1/', headers={'Device-Id': '00:11:22:33:44:55'}):
                    pass
        self.assertEqual(error.exception.code, 4401)

    def test_device_token_cannot_be_used_with_another_mac(self):
        accounts = importlib.import_module('platform-bridge.accounts')
        device_db = importlib.import_module('platform-bridge.device_db')
        with tempfile.TemporaryDirectory() as directory:
            store = accounts.AccountStore(Path(directory) / 'accounts.db')
            devices = device_db.DeviceDB(Path(directory) / 'devices.db')
            store.create_user('alice', 'a sufficiently long password')
            store.put_agent('alice', {'id': 'agent', 'name': 'A'})
            code = devices.create_activation_code('agent')['code']
            token = devices.register_device_with_code(code, '00:11:22:33:44:55')['device_token']
            with patch.object(bridge, 'accounts', store), patch.object(bridge, 'device_db', devices):
                with TestClient(bridge.app) as client:
                    with self.assertRaises(WebSocketDisconnect) as error:
                        with client.websocket_connect('/xiaozhi/v1/', headers={'Device-Id': '00:11:22:33:44:56', 'Authorization': 'Bearer ' + token}):
                            pass
            self.assertEqual(error.exception.code, 4401)

    def test_valid_device_can_exchange_messages_with_upstream(self):
        import asyncio
        import websockets
        accounts = importlib.import_module('platform-bridge.accounts')
        device_db = importlib.import_module('platform-bridge.device_db')

        class Upstream:
            async def __aenter__(self):
                self.messages = asyncio.Queue()
                return self
            async def __aexit__(self, *_):
                pass
            async def send(self, message):
                await self.messages.put(message)
            def __aiter__(self):
                return self
            async def __anext__(self):
                return await self.messages.get()

        with tempfile.TemporaryDirectory() as directory:
            store = accounts.AccountStore(Path(directory) / 'accounts.db')
            devices = device_db.DeviceDB(Path(directory) / 'devices.db')
            store.create_user('alice', 'a sufficiently long password')
            store.put_agent('alice', {'id': 'agent', 'name': 'A'})
            code = devices.create_activation_code('agent')['code']
            token = devices.register_device_with_code(code, '00:11:22:33:44:55')['device_token']
            with patch.object(bridge, 'accounts', store), patch.object(bridge, 'device_db', devices), patch.object(websockets, 'connect', return_value=Upstream()):
                with TestClient(bridge.app) as client:
                    with client.websocket_connect('/xiaozhi/v1/', headers={'Device-Id': '00:11:22:33:44:55', 'Client-Id': 'device', 'Authorization': 'Bearer ' + token}) as socket:
                        socket.send_text('hello')
                        self.assertEqual(socket.receive_text(), 'hello')
                        socket.send_bytes(b'voice')
                        self.assertEqual(socket.receive_bytes(), b'voice')


class RealWebSocketProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_device_reaches_real_websocket_upstream(self):
        import asyncio
        import os
        import socket
        import uvicorn
        import websockets

        accounts = importlib.import_module('platform-bridge.accounts')
        device_db = importlib.import_module('platform-bridge.device_db')
        with tempfile.TemporaryDirectory() as directory:
            store = accounts.AccountStore(Path(directory) / 'accounts.db')
            devices = device_db.DeviceDB(Path(directory) / 'devices.db')
            store.create_user('alice', 'a sufficiently long password')
            store.put_agent('alice', {'id': 'agent', 'name': 'A'})
            code = devices.create_activation_code('agent')['code']
            token = devices.register_device_with_code(code, '00:11:22:33:44:55')['device_token']
            async def echo(upstream):
                async for message in upstream:
                    await upstream.send(message)
            async with websockets.serve(echo, '127.0.0.1', 0) as upstream:
                upstream_port = upstream.sockets[0].getsockname()[1]
                sock = socket.socket()
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
                server = uvicorn.Server(uvicorn.Config(bridge.app, log_level='error', lifespan='off'))
                with patch.object(bridge, 'accounts', store), patch.object(bridge, 'device_db', devices), patch.dict(os.environ, {'XIAOZHI_UPSTREAM_WS': f'ws://127.0.0.1:{upstream_port}/'}):
                    task = asyncio.create_task(server.serve(sockets=[sock]))
                    try:
                        for _ in range(100):
                            if server.started:
                                break
                            await asyncio.sleep(0.01)
                        self.assertTrue(server.started)
                        async with websockets.connect(f'ws://127.0.0.1:{port}/xiaozhi/v1/', additional_headers={'Device-Id': '00:11:22:33:44:55', 'Client-Id': 'client', 'Authorization': 'Bearer ' + token}) as client:
                            await client.send(b'voice')
                            self.assertEqual(await client.recv(), b'voice')
                    finally:
                        server.should_exit = True
                        await task

    async def test_websocket_stops_forwarding_after_device_revocation(self):
        import asyncio
        import os
        import socket
        import uvicorn
        import websockets
        from websockets.exceptions import ConnectionClosed
        accounts = importlib.import_module('platform-bridge.accounts')
        device_db = importlib.import_module('platform-bridge.device_db')
        with tempfile.TemporaryDirectory() as directory:
            store = accounts.AccountStore(Path(directory) / 'accounts.db')
            devices = device_db.DeviceDB(Path(directory) / 'devices.db')
            store.create_user('alice', 'a sufficiently long password')
            store.put_agent('alice', {'id': 'agent', 'name': 'A'})
            code = devices.create_activation_code('agent')['code']
            token = devices.register_device_with_code(code, '00:11:22:33:44:55')['device_token']
            received = []
            async def echo(upstream):
                async for message in upstream:
                    received.append(message)
                    await upstream.send(message)
            async with websockets.serve(echo, '127.0.0.1', 0) as upstream:
                upstream_port = upstream.sockets[0].getsockname()[1]
                sock = socket.socket()
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
                server = uvicorn.Server(uvicorn.Config(bridge.app, log_level='error', lifespan='off'))
                with patch.object(bridge, 'accounts', store), patch.object(bridge, 'device_db', devices), patch.dict(os.environ, {'XIAOZHI_UPSTREAM_WS': f'ws://127.0.0.1:{upstream_port}/'}):
                    task = asyncio.create_task(server.serve(sockets=[sock]))
                    try:
                        for _ in range(100):
                            if server.started:
                                break
                            await asyncio.sleep(0.01)
                        async with websockets.connect(f'ws://127.0.0.1:{port}/xiaozhi/v1/', additional_headers={'Device-Id': '00:11:22:33:44:55', 'Authorization': 'Bearer ' + token}) as client:
                            await client.send('before revocation')
                            self.assertEqual(await client.recv(), 'before revocation')
                            devices.unbind_device_by_token(token)
                            await asyncio.sleep(1.05)
                            await client.send('should not reach upstream')
                            with self.assertRaises(ConnectionClosed):
                                await client.recv()
                            self.assertEqual(received, ['before revocation'])
                    finally:
                        server.should_exit = True
                        await task

    async def test_parallel_audio_checks_do_not_block_event_loop(self):
        import asyncio
        import os
        import socket
        import threading
        import time
        import uvicorn
        import websockets
        accounts = importlib.import_module('platform-bridge.accounts')
        device_db = importlib.import_module('platform-bridge.device_db')
        with tempfile.TemporaryDirectory() as directory:
            store = accounts.AccountStore(Path(directory) / 'accounts.db')
            devices = device_db.DeviceDB(Path(directory) / 'devices.db')
            store.create_user('alice', 'a sufficiently long password')
            store.put_agent('alice', {'id': 'agent', 'name': 'A'})
            code = devices.create_activation_code('agent')['code']
            token = devices.register_device_with_code(code, '00:11:22:33:44:55')['device_token']
            async def echo(upstream):
                async for message in upstream:
                    await upstream.send(message)
            async with websockets.serve(echo, '127.0.0.1', 0) as upstream:
                sock = socket.socket()
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
                server = uvicorn.Server(uvicorn.Config(bridge.app, log_level='error', lifespan='off'))
                upstream_url = f"ws://127.0.0.1:{upstream.sockets[0].getsockname()[1]}/"
                with patch.object(bridge, 'accounts', store), patch.object(bridge, 'device_db', devices), patch.dict(os.environ, {'XIAOZHI_UPSTREAM_WS': upstream_url}):
                    task = asyncio.create_task(server.serve(sockets=[sock]))
                    clients = []
                    try:
                        for _ in range(100):
                            if server.started:
                                break
                            await asyncio.sleep(0.01)
                        for _ in range(4):
                            clients.append(await websockets.connect(f'ws://127.0.0.1:{port}/xiaozhi/v1/', additional_headers={'Device-Id': '00:11:22:33:44:55', 'Authorization': 'Bearer ' + token}))
                        started = threading.Event()
                        original = devices.authenticate_device
                        def slow_check(value):
                            started.set()
                            time.sleep(0.3)
                            return original(value)
                        with patch.object(devices, 'authenticate_device', side_effect=slow_check):
                            begin = time.monotonic()
                            await asyncio.gather(*(client.send(b'audio') for client in clients))
                            self.assertTrue(await asyncio.to_thread(started.wait, 2))
                            self.assertLess(time.monotonic() - begin, 0.75)
                            self.assertEqual(await asyncio.gather(*(client.recv() for client in clients)), [b'audio'] * 4)
                    finally:
                        await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
                        server.should_exit = True
                        await task
