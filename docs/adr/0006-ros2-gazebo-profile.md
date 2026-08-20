# ADR 0006: ROS 2 and Gazebo simulation profile

**Status:** Accepted for Phase 4
**Date:** 2026-08-14
**Owner:** UMP simulation and adapter maintainers

## Decision

The Phase 4 reference simulation uses Ubuntu 24.04, ROS 2 Jazzy, and Gazebo
Harmonic. CI runs the simulation headlessly on Linux AMD64. The development
container pins the official `ros:jazzy-ros-base-noble` image by digest and
installs versioned Ubuntu packages for `ros_gz`, `gz_ros2_control`, Navigation
2, MoveIt 2, and their controller dependencies.

The ROS workspace is kept under `sim/ros2_ws`. UMP protocol and coordination
logic remain outside ROS. A lifecycle-managed adapter node maps ROS actions,
topics, diagnostics, and TF observations onto a bounded local UMP adapter API.
Gazebo models and launch files are test fixtures, not protocol dependencies.

## Reproducibility policy

- `sim/ros2/Dockerfile` pins the base image digest.
- `sim/ros2/rosdep.lock` records the ROS package names used by the profile.
- The container build records resolved Debian package versions in
  `/opt/ump/ros-packages.lock`.
- S4 uses a fixed world seed and simulation time.
- Headless execution is the conformance gate; rendering is an additional
  review artifact generated from the same world and scenario.

## Rationale

Jazzy and Harmonic are the supported ROS/Gazebo pairing on Ubuntu 24.04 and
have a support lifetime suitable for an open reference implementation. A
container makes the environment usable on Linux workstations and in CI while
allowing macOS developers to run the AMD64 profile through Docker emulation.

## Consequences

The reference image is deliberately larger than the native UMP runtime. Robots
that already provide ROS 2 can install only the adapter packages; they do not
need Gazebo or the development container. Native and vendor-bridge deployment
paths remain independent of ROS.

