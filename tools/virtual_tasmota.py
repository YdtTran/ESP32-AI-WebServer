"""Simulate a single Tasmota relay over MQTT."""

import json
import os
import time


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
    if '/' in device or not device or any(c in prefix + device for c in '+#'):
        raise ValueError('Invalid MQTT topic')
    root = f'{prefix}/' if prefix else ''
    command_topic = f'{root}cmnd/{device}/POWER'
    status_topic = f'{root}stat/{device}'
    telemetry_topic = f'{root}tele/{device}'
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
        while True:
            client.publish(f'{telemetry_topic}/STATE', json.dumps({'POWER': 'ON' if state['power'] else 'OFF'}))
            time.sleep(30)
    except KeyboardInterrupt:
        client.publish(f'{telemetry_topic}/LWT', 'Offline', retain=True).wait_for_publish(timeout=5)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == '__main__':
    main()
