"""Simulate a single Tasmota relay over MQTT."""

import json
import math
import os
import time


def timing_configuration(environment):
    delay = float(environment.get('TASMOTA_RESPONSE_DELAY', '0'))
    disconnect_after = float(environment.get('TASMOTA_DISCONNECT_AFTER', '0'))
    disconnect_duration = float(environment.get('TASMOTA_DISCONNECT_DURATION', '0'))
    if (not all(math.isfinite(value) and value >= 0 for value in (delay, disconnect_after, disconnect_duration))
            or bool(disconnect_after) != bool(disconnect_duration)):
        raise ValueError('Invalid simulator timing configuration')
    return delay, disconnect_after, disconnect_duration


def mqtt_topic(full_topic, device, prefix, suffix):
    if (full_topic.count('%prefix%') != 1 or full_topic.count('%topic%') != 1
            or any(c in full_topic + device for c in '+#') or not device or '/' in device):
        raise ValueError('Invalid MQTT FullTopic or device topic')
    return full_topic.replace('%prefix%', prefix).replace('%topic%', device).rstrip('/') + '/' + suffix


def power_command(payload, power):
    command = payload.strip().upper()
    if command in ('', '?'):
        return power
    if command in ('ON', '1', 'TRUE'):
        return True
    if command in ('OFF', '0', 'FALSE'):
        return False
    if command in ('TOGGLE', '2'):
        return not power
    raise ValueError('Unsupported POWER command')


def main():
    import paho.mqtt.client as mqtt

    host = os.environ.get('MQTT_HOST', 'localhost')
    port = int(os.environ.get('MQTT_PORT', '1883'))
    prefix = os.environ.get('TASMOTA_PREFIX', '').strip('/')
    device = os.environ.get('TASMOTA_TOPIC', 'virtual-tasmota')
    root = f'{prefix}/' if prefix else ''
    full_topic = os.environ.get('TASMOTA_FULL_TOPIC', f'{root}%prefix%/%topic%/')
    response_delay, disconnect_after, disconnect_duration = timing_configuration(os.environ)
    command_topic = mqtt_topic(full_topic, device, 'cmnd', 'POWER')
    status_topic = mqtt_topic(full_topic, device, 'stat', '').rstrip('/')
    telemetry_topic = mqtt_topic(full_topic, device, 'tele', '').rstrip('/')
    state = {'power': False}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if os.environ.get('MQTT_USERNAME'):
        client.username_pw_set(os.environ['MQTT_USERNAME'], os.environ.get('MQTT_PASSWORD'))
    client.will_set(f'{telemetry_topic}/LWT', 'Offline', retain=True)

    def publish_power():
        value = 'ON' if state['power'] else 'OFF'
        client.publish(f'{status_topic}/RESULT', json.dumps({'POWER': value}))
        client.publish(f'{status_topic}/POWER', value)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f'MQTT connection failed: {reason_code}', flush=True)
            return
        client.subscribe(command_topic)
        client.publish(f'{telemetry_topic}/LWT', 'Online', retain=True)
        publish_power()
        print(f'Virtual relay connected: {command_topic}', flush=True)

    def on_message(client, userdata, message):
        if response_delay:
            time.sleep(response_delay)
        try:
            state['power'] = power_command(message.payload.decode('utf-8'), state['power'])
        except (ValueError, UnicodeDecodeError):
            print('Ignored invalid POWER command', flush=True)
            return
        publish_power()
        print(f"POWER {'ON' if state['power'] else 'OFF'}", flush=True)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, keepalive=60)
    client.loop_start()
    try:
        next_telemetry = 0
        next_disconnect = time.monotonic() + disconnect_after if disconnect_after else None
        while True:
            now = time.monotonic()
            if next_disconnect is not None and now >= next_disconnect:
                client.publish(f'{telemetry_topic}/LWT', 'Offline', retain=True).wait_for_publish(timeout=5)
                client.disconnect()
                client.loop_stop()
                time.sleep(disconnect_duration)
                client.reconnect()
                client.loop_start()
                next_disconnect = time.monotonic() + disconnect_after
            if now >= next_telemetry:
                client.publish(f'{telemetry_topic}/STATE', json.dumps({'POWER': 'ON' if state['power'] else 'OFF'}))
                next_telemetry = time.monotonic() + 30
            time.sleep(0.1)
    except KeyboardInterrupt:
        client.publish(f'{telemetry_topic}/LWT', 'Offline', retain=True).wait_for_publish(timeout=5)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == '__main__':
    main()
