# Webots and ROS 2 Visual Demonstration

## Purpose

Webots with ROS 2 Jazzy is UMP's primary visual demonstration profile. It shows
the reference warehouse goal executing through three independently addressed
robots while retaining the same UMP capability names, ROS action interface,
planner order, and evidence vocabulary used elsewhere in this repository.

Gazebo Harmonic remains the compatibility and engineering-validation profile.
The two simulators are separate ROS packages and neither is a runtime dependency
of the UMP core.

## What the demonstration proves

The Webots run demonstrates:

- three robot drivers connected through `webots_ros2_driver`;
- physics-enabled wheeled locomotion in a rendered warehouse;
- a lidar-equipped route-inspection robot;
- the existing `WarehousePlanner` and `Ros2RobotAdapter` boundary;
- ordered ROS 2 actions for route inspection, package transport, and placement;
- the same action and canonical UMP capabilities used by the Gazebo profile; and
- a machine-readable report from the complete collaborative flow.

The humanoid and quadruped forms use differential-drive locomotion in this first
visual profile. Package attachment and final shelf placement are supervisor-driven
visual choreography. This run proves UMP-to-Webots integration and action lifecycle
behavior, not walking, gait control, grasp stability, motion planning, hardware
safety, or production readiness.

## Supported setup

Use Ubuntu 24.04, ROS 2 Jazzy, Webots, and the Jazzy `webots_ros2` packages. The
commands assume ROS 2 Jazzy is installed from the official ROS repository.

For a complete host compatibility matrix and an all-in-one Ubuntu virtual
machine procedure, use [`WEBOTS_VM.md`](WEBOTS_VM.md). Linux Webots R2025a is
published for x86-64, so an Apple Silicon Linux guest must emulate x86-64 rather
than use the default ARM64 Ubuntu image. That emulated UTM path is experimental;
use native or virtualized x86-64 Ubuntu for retained visual qualification.

```bash
sudo apt update
sudo apt install curl ros-jazzy-webots-ros2-driver python3-colcon-common-extensions
curl -fL -o /tmp/webots.deb \
  https://github.com/cyberbotics/webots/releases/download/R2025a/webots_2025a_amd64.deb
sudo apt install /tmp/webots.deb
git clone https://github.com/promiseeuler/UMP.git
cd UMP
python3 -m pip install --break-system-packages --ignore-installed -e .
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths ros2_ws/src --ignore-src -r -y
cd ros2_ws
colcon build --symlink-install
source install/setup.bash
```

Webots R2025a is pinned to the version required by the current Jazzy driver.
Webots is a desktop simulator and must be installed locally. The browser UMP
demonstration cannot provide the same simulator physics or ROS driver boundary.

## Run the visual warehouse

From the repository root after building and sourcing the workspace:

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
ros2 launch ump_webots_demo warehouse.launch.py
```

The launch opens Webots and automatically submits the reference shared task:

1. `robot_quadruped_1` inspects the route.
2. `robot_humanoid_1` carries `package-1` to the handoff.
3. `robot_mobile_arm_1` places the package on `shelf-a`.

Set `autostart:=false` to operate the action endpoints manually. In another
sourced terminal, run the ordered flow and retain its report:

```bash
share=$(ros2 pkg prefix ump_webots_demo --share)
ros2 run ump_webots_demo warehouse_smoke_client \
  --report /tmp/ump-ros2-webots-smoke.json \
  --world "$share/worlds/ump_warehouse.wbt"
```

For a headless machine with a virtual display:

```bash
xvfb-run -a timeout 240s bash ros2_ws/webots_smoke.sh
```

## UMP boundary

Each `WebotsController` loads a manufacturer-style plugin. The plugin translates
only an approved high-level assignment into native simulator activity. UMP never
sends wheel speeds, joint positions, controller gains, or emergency-stop commands.

```text
UMP goal and planner
        |
ExecuteCapability ROS 2 action
        |
UMP Webots driver plugin
        |
Webots motors, sensors, and world state
```

A physical integration replaces the final plugin with a manufacturer adapter
while preserving the UMP contracts. Follow `ROBOT_DEPLOYMENT.md` and
`HARDWARE_PILOT.md` before enabling supervised physical assignments.

Use `ROS2_GAZEBO.md` for the Gazebo Harmonic compatibility profile. Webots is the
visual and design-partner demonstration; Gazebo remains an independent lifecycle
fixture until both profiles gain retained dynamics evidence.
