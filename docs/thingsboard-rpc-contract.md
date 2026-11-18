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
Set `TASMOTA_FULL_TOPIC` to override that layout, using `%prefix%` and `%topic%`
once each, for example `site/%topic%/%prefix%/`. When set, it replaces the
layout generated from `TASMOTA_PREFIX`.

The relay accepts POWER ON, OFF, TOGGLE, 1, 0, 2, true, false, and empty or `?`
queries. It publishes Tasmota-style status messages and periodic STATE data;
its state is virtual and resets to OFF on restart. The existing ThingsBoard
Tasmota downlink converter accepts `setGpio` with `{"state": true}` or
`{"state": false}`. Its `setPower` mapping expects a primitive, not the
object described by the HTTP simulator contract. A one-way RPC confirms
delivery to the integration, not that the relay changed state; read power
telemetry to confirm the observed state. This simulator does not emulate all
Tasmota commands or publish the HTTP simulator's `rpc_contract` attribute.

## Capability-aware MCP (in progress)

The standalone ThingsBoard MCP server reads `rpcCapabilities` or `rpc_contract`
from accessible device attribute scopes. Each attribute is an object with a
`methods` map; each method may have `description` and `params`, where `params`
is a JSON Schema. RPC widgets with a direct device target also contribute method
names, but cannot establish a parameter schema. Methods with missing, invalid,
conflicting, reference-dependent, or unsupported-format schemas cannot be executed. Discovery reads
configuration only and never sends RPC. The server refreshes before execution;
its `send_rpc` tool uses the same checks as `execute_device_action`.
`available: true` means the declaration was present at discovery. The separate
availability field uses ThingsBoard's server-side `active` attribute when
present, otherwise `unknown`. ThingsBoard checks the caller's RPC permission
when the request is dispatched.

The SQLite cache is scoped to the profile and connection credentials. Set
`TB_CAPABILITY_DB` to a persistent writable path; the standalone Compose file
mounts one. A one-way result marked `submitted` does not confirm device
execution. A two-way response can report a device error.

Discovery resolves supported dashboard alias filters with ThingsBoard's
read-only entity query, using the dashboard's default sort order. Aliases that
depend on a dashboard's current state use an explicit default device when one is
configured. Otherwise they are reported with an unresolved device and
incomplete schema; they cannot be executed without a device-specific declaration.
For rule chains, a node configuration may declare `deviceId` and a structured
`rpcCapabilities` object in the same format as the device attribute. Discovery
also recognizes a fixed device originator followed directly by a transformation
that returns a literal JSON RPC message and an RPC request node. It records the
method and node references, but leaves the parameter schema incomplete unless
another source declares it. Arbitrary rule-node scripts are not evaluated or
guessed: their targets and methods can depend on runtime messages. The platform
summary and discovery tools report those rule nodes in `discovery_gaps`.
The platform bridge's existing agent RPC routes use
the same registry with the agent's credentials and profile ID. Their legacy
`rpc_methods` text alone is insufficient to authorize a command because it does
not reliably define parameter types; migrate those devices to a structured
`rpcCapabilities` or `rpc_contract` attribute. A missing registry or failed
refresh blocks RPC while leaving read-only tools available.
For a local platform bridge, install `thingsboard-mcp/requirements.txt` and keep
`thingsboard-mcp` next to `platform-bridge` on `PYTHONPATH`. The deployment
Compose file builds a bridge image with schema validation dependencies and
mounts the shared registry code read-only; rebuild it when upgrading.

## MQTT simulator and ThingsBoard gateway

Set `MQTT_USERNAME` and `MQTT_PASSWORD` in the environment, then run
`docker compose -f docker-compose.tasmota.yml up -d --build`. The authenticated
broker and two independent relays communicate on the Compose network. Set
`TASMOTA_FULL_TOPIC` to change both simulators' topic layout. Optional
`TASMOTA_RESPONSE_DELAY` delays each command, in seconds. Set both
`TASMOTA_DISCONNECT_AFTER` and `TASMOTA_DISCONNECT_DURATION` to positive seconds
to repeatedly take each simulator offline and reconnect it. Relay state persists
through simulated disconnects but resets on container restart.

To enable the gateway, create a ThingsBoard device with **Is gateway** enabled,
set its access token in `TB_GATEWAY_TOKEN`, and set `TB_MQTT_HOST` and optionally
`TB_MQTT_PORT` for the ThingsBoard MQTT listener. The default host is
`host.docker.internal`; Docker Compose maps that name to the Linux host gateway.
Run `docker compose -f docker-compose.tasmota.yml --profile thingsboard up -d
--build`. The bridge connects downstream devices when their retained LWT says
`Online`, automatically registering missing device names with ThingsBoard, and
forwards STATE and RESULT telemetry. It forwards disconnects and reconnects.

Copy `tools/tasmota-bridge.example.json` and set `TASMOTA_BRIDGE_CONFIG` to its
absolute path to customize device names, FullTopic and RPC-to-command mappings.
The ThingsBoard device names must be unique. Configure a matching
`rpcCapabilities` attribute in ThingsBoard for each device; this keeps the
platform's configuration authoritative and the MCP service does not infer
schemas from bridge mappings. For example, `setPower` in the sample mapping takes
`{"state": true}` or `{"state": false}` and its schema can be declared as:

```json
{"methods":{"setPower":{"description":"Set relay power","params":{"type":"object","properties":{"state":{"type":"boolean"}},"required":["state"],"additionalProperties":false}}}}
```

The bridge serializes commands per device and correlates each ThingsBoard request
with the next matching Tasmota RESULT. Tasmota responses have no request ID, so
the bridge must be the only command publisher for those devices. A timeout, a
disconnect with a command in flight, or a restart with outstanding commands
quarantines the device in the persistent bridge volume. Verify the relay state
and drain any delayed messages before manually clearing that device's journal
entry. The journal remembers the most recent 4096 request IDs per device;
reused IDs are rejected because Tasmota cannot distinguish a retry from a new
request. Broker and ThingsBoard MQTT connections in this development stack are
unencrypted; use a trusted network or configure TLS before remote deployment.
For physical Tasmota devices, keep the same MQTT topic and method mappings and
replace the simulator containers with real devices connected to the broker.

After verifying the physical state and draining delayed MQTT messages, clear a
quarantined relay's journal entry with the bridge stopped. Substitute the relay
topic and ThingsBoard device name from your bridge configuration:

```sh
docker compose -f docker-compose.tasmota.yml stop thingsboard-bridge
docker compose -f docker-compose.tasmota.yml run --rm --no-deps --entrypoint python thingsboard-bridge \
  -c 'import json,sqlite3,sys; db=sqlite3.connect("/data/bridge.sqlite3"); db.execute("DELETE FROM relay_state WHERE device = ?", (json.dumps(sys.argv[1:3]),)); db.commit()' \
  relay-a 'Simulated Tasmota A'
docker compose -f docker-compose.tasmota.yml --profile thingsboard up -d thingsboard-bridge
```

Clearing an entry also clears its remembered request IDs. Do not replay an
earlier command when its outcome is unknown.

The opt-in `tests/test_tasmota_thingsboard_e2e.py` creates uniquely named devices
in a disposable ThingsBoard tenant and removes them afterward. Set
`TB_TEST_HOST`, `TB_TEST_USERNAME` and `TB_TEST_PASSWORD`; optionally set
`TB_TEST_MQTT_HOST` and `TB_TEST_MQTT_PORT` as visible inside Docker. Install
`thingsboard-mcp/requirements.txt`, then run
`python -m unittest tests.test_tasmota_thingsboard_e2e`. It exercises direct
one-way and two-way RPC and MCP stdio execution using real MQTT relays.
