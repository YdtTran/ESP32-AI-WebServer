# ThingsBoard device RPC contract

The virtual switch publishes a `rpc_contract` client attribute as a JSON object.
Version 1 contains a `methods` object keyed by RPC method name. Each method has
a description and a JSON Schema describing its `params`. The virtual switch
supports `setPower` with `{"state": true}` or `{"state": false}`, and
`getState` and `togglePower` with `{}`. Its responses report `power` and
`simulated`; invalid arguments return an error without changing the state.

The existing `rpc_methods` attribute remains available for current clients.
`rpc_contract` describes only the virtual device's implemented methods; it is
not a catalog of every ThingsBoard RPC. Clients must read the contract for the
specific device and validate arguments before sending an RPC. A two-way RPC is
needed to observe the virtual device's reply.

Tasmota is a possible future device implementation. Its native MQTT Power
commands require an adapter between this contract and Tasmota's topics and
state responses; this virtual device does not implement that adapter.

## Virtual Tasmota relay

`tools/virtual_tasmota.py` provides a separate MQTT-based virtual relay for
testing a Tasmota integration. Install `tools/requirements.txt` and start it with
`MQTT_HOST`, `TASMOTA_PREFIX`, and `TASMOTA_TOPIC` set for the integration.
`MQTT_PORT` defaults to 1883; `MQTT_USERNAME` and `MQTT_PASSWORD` are optional.
Without a prefix, it uses the usual `cmnd/<topic>/POWER`, `stat/<topic>/RESULT`,
`stat/<topic>/POWER`, and `tele/<topic>/LWT` topic layout. A prefix is placed
before those paths for integrations that namespace their topics.

The relay accepts POWER ON, OFF, TOGGLE, 1, 0, 2, true, false, and empty or `?`
queries. It publishes Tasmota-style status messages and periodic STATE data;
its state is virtual and resets to OFF on restart. The existing ThingsBoard
Tasmota downlink converter accepts `setGpio` with `{"state": true}` or
`{"state": false}`. Its `setPower` mapping expects a primitive, not the
object described by the HTTP simulator contract. A one-way RPC confirms
delivery to the integration, not that the relay changed state; read power
telemetry to confirm the observed state. This simulator does not emulate all
Tasmota commands or publish the HTTP simulator's `rpc_contract` attribute.
