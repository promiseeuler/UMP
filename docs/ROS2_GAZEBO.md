# ROS 2 and Gazebo Integration Profile

## Status

The reference integration targets Ubuntu 24.04 Noble, ROS 2 Jazzy, Gazebo
Harmonic, and `ros_gz_sim`. Jazzy's
[official release documentation](https://docs.ros.org/en/jazzy/Releases/Release-Jazzy-Jalisco.html)
identifies Harmonic as its recommended Gazebo release. The UMP core remains
middleware-neutral and does not import ROS.

`.github/workflows/ros2.yml` builds and tests both ROS packages against Jazzy
using pinned official ROS tooling actions. This verifies package metadata,
interface generation, imports, and declared dependencies. It is not evidence of
robot dynamics or physical work.

This repository currently provides the tested manufacturer adapter boundary and
the concrete `rclpy` action backend. It also contains a ROS interface package,
three cancellable proxy action servers, and a Gazebo world fixture. ROS 2 and
Gazebo are not installed in the development environment used for the core test
suite, so simulator launch and physics behavior are not yet claimed as verified.

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

The launch uses the standard `ros_gz_sim` launch integration documented by
[Gazebo](https://gazebosim.org/docs/harmonic/ros2_integration/). The humanoid,
quadruped, and mobile-arm entities are static visual proxies. Their action
servers validate lifecycle interoperability, progress, and cancellation; they
do not claim locomotion, manipulation, contact, or physics fidelity.
