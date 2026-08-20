# Protocol Inspector

The UMP inspector is a local, read-only operational view of observed protocol
traffic. It exposes robot manifests, current semantic state, capabilities, and a
correlated event timeline. It has no endpoint or UI action for assignments,
cancellation, authority changes, or native robot commands.

## Record events

Attach one recorder to the same `MessageBus` used by the runtime:

```python
from ump.inspector import InspectorRecorder, InspectorStore

store = InspectorStore("var/ump-inspector.sqlite3")
recorder = InspectorRecorder(bus, store)
```

The recorder subscribes to all message types and writes canonical envelopes to
an append-only SQLite WAL database. Repeated message IDs and repeated
source/session sequence values are ignored.

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

## Operational limits

The inspector is observational, not a complete audit system. A process must
attach the recorder to capture traffic, host access still follows local machine
permissions, and database retention or export policy remains the deployer's
responsibility.
