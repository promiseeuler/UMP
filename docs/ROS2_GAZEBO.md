# ROS 2 and Gazebo Integration Profile

## Status

The reference integration targets Ubuntu 24.04 Noble, ROS 2 Jazzy, Gazebo
Harmonic, and `ros_gz_sim`. Jazzy's
[official release documentation](https://docs.ros.org/en/jazzy/Releases/Release-Jazzy-Jalisco.html)
identifies Harmonic as its recommended Gazebo release. The UMP core remains
middleware-neutral and does not import ROS.

`.github/workflows/ros2.yml` builds and tests both ROS packages against Jazzy
using a pinned official setup action, then runs a bounded headless lifecycle
smoke test on native Noble. Static proxy geometry and lifecycle success are not
evidence of robot dynamics or physical work.

This repository provides the tested manufacturer adapter boundary and concrete
`rclpy` action backend. It also contains a generated ROS action interface, three
cancellable proxy action servers, and a Gazebo world fixture. Both ROS packages
have been built successfully with `colcon` against ROS 2 Jazzy and Gazebo Sim
8.11.0 in a Linux amd64 container. The package entry points and generated action
types load successfully there.

Cross-process DDS discovery and Gazebo Transport cannot run under the current
Apple Silicon host's amd64/qemu Docker network emulation: both fail during
network-interface discovery before processing UMP behavior. The native Ubuntu
Noble workflow therefore owns runtime evidence. Its bounded headless smoke test
requires the Gazebo world service, three action servers, successful goals,
capability rejection, and cancellation. Until that workflow result is inspected,
simulator runtime and physics behavior remain unverified.

The smoke writes `/tmp/ump-ros2-gazebo-smoke.json` only after every lifecycle
check succeeds. CI validates that report against the checked-in world digest and
the exact repository revision, then retains it as a 30-day workflow artifact.
Validate a downloaded or locally produced report with:

```sh
ump-ros2-evidence /tmp/ump-ros2-gazebo-smoke.json \
  --world ros2_ws/src/ump_gazebo_demo/worlds/three_robot_world.sdf \
  --revision COMMIT_SHA
```

The validator requires Jazzy, all six readiness/lifecycle checks, and the exact
success, adapter success, cancellation, and capability-rejection results. This
is native middleware lifecycle evidence. It remains explicitly insufficient to
claim locomotion, manipulation, contact, or physics fidelity.

The native smoke also constructs the public `Ros2RobotAdapter`,
`RclpyActionBackend`, generated `ExecuteCapability` binding, and a structured UMP
assignment. It requires that assignment to cross the manufacturer adapter
boundary and return a successful structured UMP outcome. Separate direct action
checks retain native success, capability rejection, and cancellation coverage.

## Why ROS actions

UMP capabilities represent high-level work that may run for seconds or minutes,
publish progress, return a result, and support cancellation. ROS actions provide
that lifecycle directly. Topics remain appropriate for manufacturers to feed
continuous observations into `SemanticStateStore`; services may support short
native checks, but are not used as the long-running assignment boundary.
This follows the [ROS interface guidance](https://docs.ros.org/en/jazzy/How-To-Guides/Topics-Services-Actions.html).

## Adapter boundary

`Ros2RobotAdapter` implements the same `RobotAdapter` contract used by the
deterministic simulation:

- `manifest()` returns explicitly advertised capabilities;
- `state()` reads a thread-safe semantic state written by trusted ROS callbacks;
- `accept()` selects the exact `RosActionBinding` for the requested capability;
- `cancel()` delegates to the active ROS action goal handle.

Each manufacturer owns its action type and the two conversion functions:

```python
binding = RosActionBinding(
    capability="acme.navigation.inspect/v1",
    action_name="/acme/inspect_route",
    action_type=InspectRoute,
    goal_builder=build_inspect_goal,
    result_reader=read_inspect_result,
)
```

`goal_builder` maps already validated UMP inputs to a high-level ROS action goal.
It must not let generic UMP code invent trajectories or actuator commands.
`result_reader` maps the native action status and result to `NativeActionResult`.

## Runtime threading

`RclpyActionBackend` uses asynchronous action futures but presents the blocking
adapter method required by the current participant worker. The supplied ROS node
must therefore spin in a separate `MultiThreadedExecutor` thread. Calling it from
the executor callback thread can deadlock and is unsupported.

If an action result times out, the backend requests cancellation and reports
`unknown`; it never converts uncertainty into failure or success. UMP retains
resource reservations for that assignment until durable evidence or an approved
operator/domain process resolves it.

## Safety

ROS action cancellation is task coordination, not an emergency stop. Gazebo and
physical deployments must retain robot-local controller limits, collision
handling, safety state publication, and external emergency-stop mechanisms.

## Build and launch

On Ubuntu with ROS 2 Jazzy, Gazebo Harmonic, `ros_gz`, and `colcon` installed:

```sh
source /opt/ros/jazzy/setup.bash
cd ros2_ws
colcon build --symlink-install
source install/setup.bash
ros2 launch ump_gazebo_demo three_robot_world.launch.py
```

For the bounded headless evidence run, execute `bash smoke.sh` from `ros2_ws`
after sourcing the workspace. Set `UMP_SMOKE_REPORT` to choose a report path.

The launch uses the standard `ros_gz_sim` launch integration documented by
[Gazebo](https://gazebosim.org/docs/harmonic/ros2_integration/). The humanoid,
quadruped, and mobile-arm entities are static visual proxies. Their action
servers validate lifecycle interoperability, progress, and cancellation; they
do not claim locomotion, manipulation, contact, or physics fidelity.
