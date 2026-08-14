# ROS 2 adapter mapping

## Package installation

The supported profile is Ubuntu 24.04 with ROS 2 Jazzy. Install and verify the
matching native runtime package first, then the architecture-matched ROS package:

```sh
sudo apt install ./ump_0.1.0_amd64.deb
sudo apt install ./ump-ros2-jazzy_0.1.0_amd64.deb
source /opt/ros/jazzy/setup.bash
source /opt/ump/ros/jazzy/setup.bash
ros2 pkg prefix ump_ros2_adapter
ros2 interface show ump_interfaces/action/ExecuteCapability
```

Use the `arm64` artifacts on ARM robot computers. Official artifacts must pass
the checksum, Sigstore, and provenance checks in the release verification guide
before installation.

The package installs three discoverable packages under `/opt/ump/ros/jazzy`:
`ump_interfaces`, `ump_ros2_adapter`, and `ump_gazebo`. It contains generated
interfaces, lifecycle nodes, parameters, launch files, the S4 models/world, and
the payload and zone plugins. `/etc/profile.d/ump-ros2-jazzy.sh` makes the
profile available to login shells; explicit sourcing is preferred in services.

`make ros2-package-test` builds at the final install prefix and installs the
artifact into a shell that has not sourced the development workspace. The gate
rejects embedded development prefixes and verifies package discovery, interface
introspection, Python imports, launch metadata, and the native-runtime dependency.

## Migration workflow

1. Keep the robot's existing Nav2, MoveIt, vendor controller, diagnostics, and
   certified safety chain in place.
2. Initialize `umpd` and register only the capabilities the robot will expose.
3. Copy the closest adapter parameter file and map its action, diagnostics, TF,
   localization uncertainty, and safety topics to the robot's existing APIs.
4. Launch the adapter inactive, run `ump doctor`, and inspect TF and diagnostics.
5. Activate read-only state publication before granting any command lease.
6. Exercise cancellation, deadline, communication-loss, protective-stop, and
   emergency-stop behavior in simulation before enabling physical motion.

Moving to UMP changes the coordination boundary, not the robot's motion planner
or safety authority. Unsupported or unresolved mappings must remain unavailable
rather than being advertised optimistically.

The reference adapter is a lifecycle-managed ROS 2 node. It keeps ROS names,
message types, controller actions, and TF frame names outside the UMP protocol.
`umpd` remains responsible for peer authentication, authority leases,
idempotency, durable task state, resources, and handoff records.

## Runtime flow

1. A remote peer submits a task over authenticated UMP transport.
2. `umpd` validates the lease and capability, then journals `accepted`.
3. The active ROS adapter asks the local Unix socket for `next_task`.
4. `umpd` revalidates authority, journals `running`, and returns one execution
   permit with the immutable task context.
5. The adapter sends `ExecuteCapability` to a robot-specific ROS controller.
6. Controller feedback becomes local `progress` calls. The final ROS action
   result becomes one local `complete` call and a durable terminal task state.

The socket is `${UMP_DATA_DIR}/adapter.sock`, is mode `0600`, accepts one
newline-delimited JSON request per connection, and has a 96 KiB request limit.
API version 1 provides `ping`, `next_task`, `task_status`, `progress`,
`complete`, `state_update`, authority-scoped resource revocation, and
authority-scoped handoff subject-fault reporting. The interface is local-only
and is not a substitute for UMP's
mutually authenticated network transport.

## S4 execution

`make ros2-s4` builds the pinned image and runs the complete headless fixture.
The fixture creates independent coordinator, mobile-base, and robot-arm
identities under `/run/ump`; enrolls their certificates; grants separate,
capability-scoped leases; starts one `umpd` per robot; and launches
`s4_stack.launch.py`.

The coordinator uses authenticated UMP task commands, while arm and mobile use
`reserve-resource`, `propose-handoff`, `update-handoff`, and `query-handoff`
under their own identities. No participant imports another machine's ROS
packages, reads executor journals, or accesses Gazebo topics. The
nominal task sequence and terminal reconciliation records are written to
`/tmp/ump-s4-trace.jsonl`, with a machine-readable invariant report beside it.
The containerized target exports these as `artifacts/s4/trace.jsonl` and
`artifacts/s4/invariants.json`. It also exports inspector snapshots and a
Linux process-resource report at `artifacts/s4/performance.json`. Runtime and
ROS logs are under `/tmp/ump-s4`.

Use `make ros2-test` for package-level tests and `make ros2-smoke` for the short
Gazebo entity/plugin gate. `make ros2-s4` is the physical shared-task checkpoint
and is expected to run on native Ubuntu 24.04 CI or an equivalent x86-64 host.
`make ros2-s4-faults` runs the same native stack, verifies stale and
discontinuous TF plus excessive localization-uncertainty rejection and recovery,
spawns an unapproved model inside the instrumented transfer zone, verifies the
local zone authority revokes the active handoff reservation, then spawns
`blocked_path_obstacle.sdf` and requires collision-induced navigation stall to
reach a failed UMP task with `ros.navigation_blocked` in its timeline.

The Gazebo monitor publishes only `clear` or `intrusion:<model>` observations.
The `zone_monitor` ROS node translates an intrusion into the bounded local
`resource_intrusion` method. `umpd` finds active claims and performs revocation;
the monitor cannot commit, abort, or change package ownership directly.

The `payload_monitor` node translates a physical `dropped` observation into
`handoff_subject_fault`. The coordination authority resolves active handoffs
for that subject. A drop before transfer fails the handoff; a drop during
transfer produces inspection-required `unknown` while retaining the last
authoritative owner. The monitor cannot select a new owner or commit a handoff.

## ROS mappings

| ROS surface | UMP meaning |
| --- | --- |
| `ExecuteCapability` goal | One permitted task attempt |
| Action feedback | Task progress and stage |
| Action result | Succeeded, failed, retryable, or cancelled outcome |
| `/diagnostics` | Operational state, safety state, and health codes |
| `~/safety` | Immediate protective-stop, emergency-stop, or recovery-required override |
| TF lookup | Timestamped spatial observation with explicit validity |
| Localization uncertainty topic | Position/orientation uncertainty and validity limits |
| Lifecycle inactive/finalized | Adapter unavailable; no new work is claimed |

The S4 fixture maps `org.ump.navigation.navigate` to differential-drive
velocity commands closed against odometry. It maps
`org.ump.manipulation.arm_pose` to Gazebo joint-position topics. These are test
adapters; a production robot maps the same UMP capabilities to its approved
Nav2, MoveIt, vendor SDK, or controller interface.

The fixture's navigation controller requires at least 3 cm of progress within
four seconds and reports `ros.navigation_blocked` with a bounded retry delay
when an obstacle prevents motion. Gazebo fault topics
`/ump/payload/fail_next_attach` and `/ump/payload/drop` exercise failed grasp
and dropped-payload outcomes without pretending that ownership committed.

## Mobile model contract

The warehouse world includes `model://ump_mobile_base` under the stable
instance name `mobile_base`. The reference and compact alternate resources
have different geometry, mass, wheel radius, and track width, but expose the
same `base_link`, velocity topic, and odometry topic expected by the S4 adapter
profile. `UMP_MOBILE_MODEL_VARIANT=alternate` changes only Gazebo resource
selection; coordinator logic and UMP task inputs are unchanged.

`make ros2-s4-model-swap` runs the complete nominal mission against the
alternate model and writes an independent trace and invariant report under
`artifacts/s4-model-swap`. Its performance report uses the same sampling and
acceptance profile as the reference model. The package test checks both model
contracts and proves their physical parameter signatures differ.

## Inspector evidence

`ump inspector` emits one read-only JSON snapshot for the local trust boundary.
It includes identity and protocol version, runtime operational/safety/health
state, peers, trust permissions, capabilities, tasks, leases, resources,
reservations, handoffs, task and coordination histories, and a fault summary.
It never reads another machine's data directory and excludes certificates and
private keys. `--output PATH` writes the same snapshot printed to stdout.

S4 packages snapshots for coordinator, mobile, arm, and zone runtimes under
each artifact directory's `inspector/` folder. `verify_inspector.py` applies a
nominal or fault-specific conformance profile and writes `report.json`.

S4 nodes share Gazebo simulation time. Mobile odometry broadcasts
`world -> mobile_base/base_link`, while the fixture publishes the arm-tool
reference frame. TF samples older than 500 ms, more than 50 ms in the future,
non-finite, non-normalized, or discontinuous beyond configured translation and
rotation limits are published as invalid with a stable `tf.*` reason code.
Position and orientation uncertainty are always populated. A robot may provide
fixed declared values or configure `localization_uncertainty_topic` as a
timestamped `LocalizationUncertainty` observation. Missing, stale, non-finite,
negative, or values
above the configured capability limits are invalid with a stable
`localization.*` reason code.

## Safety behavior

- Missing diagnostics map to degraded operation and unknown safety.
- Diagnostic level 2 maps to faulted/recovery-required.
- Diagnostic level 3 maps to faulted/emergency-stop.
- Expired task deadlines are rejected before controller dispatch and enforced
  throughout navigation, arm movement, and payload operations.
- Controller absence produces a retryable failure without physical action.
- Sustained navigation stall stops the base and produces a retryable blocked-path failure.
- Failed grasp and dropped-payload states never produce successful attachment evidence.
- Invalid or discontinuous TF is surfaced as invalid spatial context and cannot
  satisfy the runtime's fresh spatial preconditions.
- Protective stop, emergency stop, recovery-required, lease loss, and unknown
  task authority cancel an active ROS action.
- Once a protective, emergency, or recovery-required safety event is observed,
  the adapter latches it. Lower-severity and `normal` ROS events cannot clear or
  downgrade the stop; no remote reset path is exposed.
- New work is not claimed while a protective, emergency, or recovery-required
  safety state is active.
- Adapter disconnect leaves the durable task available or marks an interrupted
  running task for reconciliation after daemon recovery.
