# Simulated Qualification

`ump-simulate` runs the repeatable development simulation that precedes native
ROS 2, two-host, manufacturer, and physical-pilot qualification. It exercises
the real protocol models, coordinator, adapter boundary, in-memory transport,
communication watchdog, and mutual-TLS loopback implementation.

From an installed package or repository checkout:

```sh
ump-simulate run --project-root . \
  --tls-samples 25 \
  --output compliance/simulated-qualification.json

ump-simulate validate compliance/simulated-qualification.json
```

From a source checkout without installation, prefix the commands with
`PYTHONPATH=src python3 -m ump.cli simulate`.

The run checks:

- manifests and semantic state from three heterogeneous simulated robots reach
  an observer associated with every robot;
- one shared warehouse goal becomes three capability-based assignments;
- route inspection, transport, and placement dependencies dispatch in order;
- every assignment has correlated acknowledgement and outcome messages;
- missing local authority leases fail closed;
- cyclic plans are rejected before dispatch;
- stale and restored peer state invoke adapter-owned communication policy;
- same-host TCP loopback communication passes mutual-TLS and reference latency
  checks; and
- all simulated adapters pass read-only contract inspection.

The JSON report uses profile `ump.simulated-qualification/v1`, identifies the
exact source revision and environment, and lists every check. It always declares
`qualification_substitute: false`. The validator rejects any report that tries
to present simulation as production qualification.

This run does not exercise physical dynamics, actuators, emergency stops, a
representative two-host LAN, independent manufacturer code, or external review.
It therefore does not change any gate in `compliance/qualification.json`.

For professional robot simulation, run the native ROS 2 Jazzy and Gazebo
Harmonic workflow in `ROS2_GAZEBO.md`. For physical progression, follow
`ROBOT_DEPLOYMENT.md` and retain the evidence described in `HARDWARE_PILOT.md`.
