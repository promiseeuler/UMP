# Manufacturer Adapter Guide

## Boundary

A UMP adapter translates manufacturer-native semantic state and high-level task
APIs. It must not translate UMP messages into joint commands, trajectories,
motor values, or safety-system actions. Native software remains authoritative
for acceptance, execution, cancellation, collision handling, emergency stops,
and all physical safety behavior.

The documented Python contract is available from `ump.adapter.RobotAdapter` and
from the package root:

```python
from ump import RobotAdapter
```

An implementation supplies four methods:

- `manifest()` returns stable identity and currently advertised capabilities.
- `state()` returns a bounded semantic snapshot with a freshness interval.
- `accept()` passes an already authorized, schema-valid high-level request to
  native software and returns its terminal outcome.
- `cancel()` asks native software to cancel and reports its decision. It is not
  an emergency stop.

`RobotAdapter` is a structural protocol; adapters do not need to inherit from a
UMP base class. This keeps vendor SDK ownership and process architecture local.

Deployments that declare required peers also require the optional
`CommunicationLossHandler` protocol. Its `communication_lost()` and
`communication_restored()` callbacks receive exact peer IDs and receiver-local
observation time. The adapter defines the local response; callback exceptions are
recorded and retried on the next watchdog evaluation rather than treated as a
successful policy transition.

## Start read-only

Begin hardware integration with an empty capability tuple. The robot can publish
state and observe authorized peers, but no UMP assignment can target it. The
runnable `examples/read_only_adapter.py` demonstrates this mode and runs the
read-only conformance inspection:

```sh
python examples/read_only_adapter.py
```

Replace only the example's state values with bounded data from the native API.
The summary must faithfully describe the structured fields. Choose
`fresh_for_ms` from the real publication period and failure-detection policy;
the core profile allows 1 to 60,000 ms.

## Advertise a capability

Use a contract from `ump.standard/v1` when its semantics match exactly:

```python
from ump import standard_capability

carry = standard_capability("ump.material.carry/v1")
```

Otherwise use a manufacturer-owned, versioned name and Draft 2020-12 input and
output schemas. Numeric robotics fields require supported `x-ump-unit` values;
spatial fields also require an explicit frame property. Advertise only behavior
that the current robot configuration can support, and update availability when
native resources become busy, degraded, or unavailable.

Inside `accept()`:

1. Confirm the native controller still supports the requested capability.
2. Apply local operating-mode, resource, workspace, and safety checks.
3. Ask the native high-level API to accept or reject the request.
4. Return the native terminal status without inventing success after uncertainty.
5. On success, return structured outputs conforming to the advertised schema.

UMP validates authority and capability input schema before calling the adapter,
but those checks do not replace native policy. Do not retry physical work merely
because a network response was lost.

## Conformance progression

Read-only inspection does not invoke native behavior:

```python
from ump import AdapterConformanceHarness

report = AdapterConformanceHarness().inspect(adapter)
```

The supported evidence command performs the same read-only operation and binds
the result to the loaded implementation file:

```sh
ump-adapter-conformance inspect \
  --adapter your_package.adapter:create_adapter \
  --adapter-config adapter.json \
  --output adapter-conformance.json
```

Provide the exact implementation file with the report so a reviewer can bind
the retained result back to source:

```sh
ump-adapter-conformance verify adapter-conformance.json \
  --implementation your_package/adapter.py
```

Execution fixtures require an explicit gate:

```python
report = AdapterConformanceHarness().exercise(
    adapter,
    controlled_assignments,
    allow_native_execution=True,
)
```

Run execution checks only in a simulator or approved bounded work area with
operator supervision and functioning physical emergency stops. Passing the
harness proves protocol compatibility, not physical safety certification.

## Deployment checklist

- Assign a stable robot identity and issue a certificate containing exactly one
  matching `urn:ump:robot:<robot-id>` URI identity.
- Enroll credentials locally and configure peer disclosure separately from task
  authority.
- Keep remote assignment authority deny-by-default during read-only rollout.
- Configure required-peer communication-loss behavior in native software.
- Persist assignment, replay, inbox, outbox, authority, and credential stores on
  durable local storage.
- Validate in deterministic simulation, network-process tests, professional robot
  simulation, hardware-in-the-loop, and supervised physical operation in order.
- Record adapter version, firmware version, robot configuration, test fixtures,
  conformance report, and operator approval for each released adapter build.
