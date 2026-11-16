#!/bin/sh
set -eu
: "${MQTT_USERNAME:?MQTT_USERNAME is required}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD is required}"
config_dir=$(mktemp -d /tmp/mosquitto.XXXXXX)
chmod 755 "$config_dir"
printf '%s\n%s\n' "$MQTT_PASSWORD" "$MQTT_PASSWORD" | mosquitto_passwd -c "$config_dir/passwords" "$MQTT_USERNAME"
chown mosquitto:mosquitto "$config_dir/passwords"
printf 'listener 1883\nallow_anonymous false\npassword_file %s/passwords\n' "$config_dir" > "$config_dir/mosquitto.conf"
exec mosquitto -c "$config_dir/mosquitto.conf"
