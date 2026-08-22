# Protocol Inspector

The UMP inspector is a local, read-only operational view of observed protocol
traffic. It exposes robot manifests, current semantic state, capabilities, and a
correlated event timeline. It has no endpoint or UI action for assignments,
cancellation, authority changes, or native robot commands.

The inspector does not discover arbitrary hardware, scan ROS graphs, or connect
directly to manufacturer SDKs. A robot appears only after a running UMP node has:

1. loaded a manufacturer adapter;
2. published an authenticated UMP manifest and semantic state; and
3. recorded those messages in the exact inspector database being served.

With no recorded messages, the production UI deliberately shows `0` robots,
`0` events, and `No UMP robots observed`. It never inserts demonstration robots
or inferred telemetry.

## What connected users see

For each genuinely observed robot, the UI shows disclosed fields only:

- stable robot identity, manufacturer, model, and robot class;
- operating mode, safety condition, operational health, and state freshness;
- battery level, charging status, observation time, and estimated runtime when
  supplied by the adapter;
- current activity, intent, progress, assignment, resources, and blockers;
- advertised high-level capabilities;
- optional pose metadata and bounded sensor references; and
- correlated manifests, states, assignments, cancellations, plans, and outcomes.

Unknown, omitted, or unavailable native telemetry remains visibly unknown. UMP
and the inspector must not estimate battery, health, task completion, or physical
state.

## Record events

Supported owner services can attach the recorder directly:

```sh
ump-node ... --inspector-database /var/lib/ump/node-inspector.sqlite3
ump-coordinator submit ... \
  --inspector-database /var/lib/ump/coordinator-inspector.sqlite3
```

The path must be unique from every other runtime database role. Participant and
coordinator preflight validate it without creating the file. The ready event
reports `inspector_recording: true` when capture is active.

Embedded runtimes may attach one recorder to the same `MessageBus`:

```python
from ump.inspector import InspectorRecorder, InspectorStore

store = InspectorStore("var/ump-inspector.sqlite3")
recorder = InspectorRecorder(bus, store)
```

The recorder subscribes to all message types and writes canonical envelopes to
an append-only SQLite WAL database. Repeated message IDs and repeated
source/session sequence values are ignored.

Recorder write failures are latched instead of propagating through the message
handler that already processed an inbound envelope. Supported owner services
include that latch in recurring runtime health checks and fail visibly after a
recording fault.

## Serve the UI

From an installed package:

```sh
ump-inspector --database var/ump-inspector.sqlite3 --port 8765
```

From a source checkout:

```sh
PYTHONPATH=src python3 -m ump.cli inspector \
  --database var/ump-inspector.sqlite3 --port 8765
```

Open `http://127.0.0.1:8765`. The UI polls the local snapshot endpoint and can
run while another process appends to the database. The server accepts only
`127.0.0.1`, `::1`, or `localhost` in v0.1 and sends restrictive content security,
cache, referrer, and content-type headers.

`ump-inspector` requires an existing compatible recorder database and opens it
with SQLite `mode=ro` and `query_only`. It validates the read-model columns before
binding the HTTP listener. A missing, mistyped, unreadable, or incompatible path
returns exit status `2`; the command does not create a parent directory, database,
table, or protocol event. Schema creation remains confined to the explicitly
enabled participant/coordinator recording path or an embedded `InspectorStore`.

The database passed to `ump-inspector` must be the same path supplied to the
active `ump-node` or coordinator through `--inspector-database`. Serving a
different compatible database is valid, but it will show only the traffic
recorded in that database.

## Operational limits

The inspector is observational, not a complete audit system. Recording must be
enabled to capture traffic, host access still follows local machine
permissions, and database retention or export policy remains the deployer's
responsibility. A `Live` indicator means the browser can reach the local
inspector server; it does not by itself mean that any robot is connected. Use
robot state freshness and network diagnostics to assess participant connectivity.
