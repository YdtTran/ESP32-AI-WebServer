"""Bridge configured Tasmota relays to the ThingsBoard MQTT gateway API."""

from collections import OrderedDict, deque
from contextlib import closing
import json
import os
import sqlite3
import threading
import time

from .virtual_tasmota import mqtt_topic


class TasmotaBridge:
    def __init__(self, devices, publish_command, publish_response, clock=time.monotonic, timeout=5, journal=None):
        self.devices = devices
        self.publish_command = publish_command
        self.publish_response = publish_response
        self.clock = clock
        self.timeout = timeout
        self.journal = str(journal) if journal else None
        self.lock = threading.RLock()
        self.states = {topic: {'online': False, 'blocked': False, 'pending': None,
                               'queue': deque(), 'seen': OrderedDict()} for topic in devices}
        self.by_name = {device['name']: topic for topic, device in devices.items()}
        if len(self.by_name) != len(devices):
            raise ValueError('ThingsBoard device names must be unique')
        if self.journal:
            with closing(sqlite3.connect(self.journal)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS relay_state (device TEXT PRIMARY KEY, state TEXT NOT NULL)')
                for topic, device in devices.items():
                    row = db.execute('SELECT state FROM relay_state WHERE device = ?',
                                     (json.dumps([topic, device['name']]),)).fetchone()
                    if row:
                        saved = json.loads(row[0])
                        state = self.states[topic]
                        state['blocked'] = saved['blocked'] or saved['inflight']
                        state['seen'] = OrderedDict((request_id, response or {'interrupted': True})
                                                    for request_id, response in saved['seen'])

    def _save(self, topic):
        if self.journal:
            state = self.states[topic]
            saved = {'blocked': state['blocked'], 'inflight': bool(state['pending'] or state['queue']),
                     'seen': list(state['seen'].items())}
            key = json.dumps([topic, self.devices[topic]['name']])
            with closing(sqlite3.connect(self.journal)) as db, db:
                db.execute('INSERT OR REPLACE INTO relay_state VALUES (?, ?)', (key, json.dumps(saved)))

    def _reply(self, topic, request_id, data):
        response = {'device': self.devices[topic]['name'], 'id': request_id, 'data': data}
        self.states[topic]['seen'][request_id] = response
        self._save(topic)
        self.publish_response(response)

    def _fail_pending(self, topic, message):
        state = self.states[topic]
        if state['pending']:
            request_id = state['pending']['id']
            state['pending'] = None
            state['blocked'] = True
            self._reply(topic, request_id, {'error': message})
        while state['queue']:
            self._reply(topic, state['queue'].popleft()['id'], {'error': message})
        self._save(topic)

    def set_online(self, topic, online):
        with self.lock:
            state = self.states[topic]
            state['online'] = online
            if not online:
                self._fail_pending(topic, 'Device disconnected; command outcome is unknown')

    def request(self, message):
        if not isinstance(message, dict):
            return
        device_name = message.get('device')
        topic = self.by_name.get(device_name) if isinstance(device_name, str) else None
        data = message.get('data')
        if topic is None or not isinstance(data, dict) or 'id' not in data:
            return
        request_id = data['id']
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            return
        with self.lock:
            state = self.states[topic]
            if request_id in state['seen']:
                if state['seen'][request_id] is not None:
                    self.publish_response({'device': self.devices[topic]['name'], 'id': request_id,
                                           'data': {'error': 'Request ID already used; command not repeated'}})
                return
            # shortcut: deduplication is retained for the latest 4096 requests per device, use durable IDs for longer replay windows.
            if len(state['seen']) >= 4096:
                if next(iter(state['seen'].values())) is None:
                    self.publish_response({'device': self.devices[topic]['name'], 'id': request_id,
                                           'data': {'error': 'Too many outstanding requests'}})
                    return
                state['seen'].popitem(last=False)
            state['seen'][request_id] = None
            if not state['online'] or state['blocked']:
                self._reply(topic, request_id, {'error': 'Device offline or awaiting manual recovery'})
                return
            method = data.get('method')
            mapping = self.devices[topic].get('methods', {}).get(method) if isinstance(method, str) else None
            if not mapping:
                self._reply(topic, request_id, {'error': 'No command mapping for method'})
                return
            params = data.get('params', {})
            if 'parameter' in mapping:
                if not isinstance(params, dict) or mapping['parameter'] not in params:
                    self._reply(topic, request_id, {'error': 'Missing command parameter'})
                    return
                params = params[mapping['parameter']]
            if 'values' in mapping:
                payload = mapping['values'].get(json.dumps(params, sort_keys=True, separators=(',', ':')))
                if payload is None:
                    self._reply(topic, request_id, {'error': 'Unsupported command parameter'})
                    return
            else:
                payload = mapping.get('payload', '')
            state['queue'].append({'id': request_id, 'command': mapping['command'], 'payload': payload})
            self._save(topic)
            self._start_next(topic)

    def _start_next(self, topic):
        state = self.states[topic]
        if state['pending'] or not state['queue'] or state['blocked'] or not state['online']:
            return
        request = state['queue'].popleft()
        request['deadline'] = self.clock() + self.timeout
        state['pending'] = request
        self._save(topic)
        full_topic = self.devices[topic].get('full_topic', '%prefix%/%topic%/')
        self.publish_command(mqtt_topic(full_topic, topic, 'cmnd', request['command']), request['payload'])

    def result(self, topic, result):
        with self.lock:
            state = self.states[topic]
            if state['pending'] is None or not isinstance(result, dict):
                return
            request = state['pending']
            if self.clock() >= request['deadline']:
                self._fail_pending(topic, 'Device response timed out; command outcome is unknown')
                return
            if request['command'] not in result:
                return
            state['pending'] = None
            self._reply(topic, request['id'], result)
            self._start_next(topic)

    def expire(self):
        with self.lock:
            for topic, state in self.states.items():
                if state['pending'] and self.clock() >= state['pending']['deadline']:
                    self._fail_pending(topic, 'Device response timed out; command outcome is unknown')


def main():
    import paho.mqtt.client as mqtt

    with open(os.environ['TASMOTA_BRIDGE_CONFIG'], encoding='utf-8') as file:
        devices = json.load(file)['devices']
    broker = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    gateway = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    broker.username_pw_set(os.environ['MQTT_USERNAME'], os.environ['MQTT_PASSWORD'])
    gateway.username_pw_set(os.environ['TB_GATEWAY_TOKEN'])

    def publish_gateway(topic, payload):
        gateway.publish(topic, json.dumps(payload), qos=1)

    bridge = TasmotaBridge(devices, lambda topic, payload: broker.publish(topic, payload),
                           lambda payload: publish_gateway('v1/gateway/rpc', payload),
                           timeout=float(os.getenv('TASMOTA_RPC_TIMEOUT', '5')),
                           journal=os.getenv('TASMOTA_BRIDGE_DB', '/data/bridge.sqlite3'))
    topics = {}
    for topic, device in devices.items():
        full_topic = device.get('full_topic', '%prefix%/%topic%/')
        for prefix, suffix in (('tele', 'LWT'), ('tele', 'STATE'), ('stat', 'RESULT')):
            topics[mqtt_topic(full_topic, topic, prefix, suffix)] = (topic, suffix)

    def broker_connected(client, userdata, flags, reason, properties):
        if not reason.is_failure:
            for topic in topics:
                client.subscribe(topic)

    def broker_disconnected(client, userdata, flags, reason, properties):
        for topic, device in devices.items():
            bridge.set_online(topic, False)
            publish_gateway('v1/gateway/disconnect', {'device': device['name']})

    def broker_message(client, userdata, message):
        topic, kind = topics[message.topic]
        device = devices[topic]
        try:
            payload = message.payload.decode('utf-8')
            if kind == 'LWT':
                online = payload == 'Online'
                bridge.set_online(topic, online)
                publish_gateway('v1/gateway/connect' if online else 'v1/gateway/disconnect',
                                {'device': device['name'], **({'type': device.get('type', 'default')} if online else {})})
            elif kind == 'STATE':
                publish_gateway('v1/gateway/telemetry', {device['name']: [json.loads(payload)]})
            else:
                result = json.loads(payload)
                publish_gateway('v1/gateway/telemetry', {device['name']: [result]})
                bridge.result(topic, result)
        except (ValueError, UnicodeDecodeError):
            print(f'Invalid MQTT message on {message.topic}', flush=True)

    def gateway_connected(client, userdata, flags, reason, properties):
        if reason.is_failure:
            return
        client.subscribe('v1/gateway/rpc')
        for topic, state in bridge.states.items():
            if state['online']:
                device = devices[topic]
                publish_gateway('v1/gateway/connect', {'device': device['name'], 'type': device.get('type', 'default')})

    def gateway_message(client, userdata, message):
        try:
            bridge.request(json.loads(message.payload))
        except (ValueError, UnicodeDecodeError):
            print('Invalid ThingsBoard gateway RPC payload', flush=True)

    broker.on_connect = broker_connected
    broker.on_disconnect = broker_disconnected
    broker.on_message = broker_message
    gateway.on_connect = gateway_connected
    gateway.on_message = gateway_message
    broker.connect(os.getenv('MQTT_HOST', 'mqtt'), int(os.getenv('MQTT_PORT', '1883')))
    gateway.connect(os.environ['TB_MQTT_HOST'], int(os.getenv('TB_MQTT_PORT', '1883')))
    broker.loop_start()
    gateway.loop_start()
    try:
        while True:
            bridge.expire()
            time.sleep(0.1)
    finally:
        broker.disconnect()
        gateway.disconnect()
        broker.loop_stop()
        gateway.loop_stop()


if __name__ == '__main__':
    main()
