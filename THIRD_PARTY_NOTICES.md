# Third-Party Notices

UMP is independently implemented. Compatibility modules use published standards
and documented interfaces; no third-party source code is copied into UMP by
default.

## Referenced specifications

| Standard | Reference | Use in UMP |
| --- | --- | --- |
| MassRobotics AMR Interoperability | <https://github.com/MassRobotics-AMR/AMR_Interop_Standard> | AMR identity and status mapping |
| VDA 5050 | <https://github.com/VDA5050/VDA5050> | Mobile robot state and authorized order mapping |
| Open-RMF | <https://github.com/open-rmf/rmf> | Fleet and facility task bridge |
| ROS 2 | <https://docs.ros.org/> | Optional robot middleware binding |
| OPC UA for Robotics | <https://reference.opcfoundation.org/specs/OPC-40010-1/full> | Industrial robot telemetry mapping |

Each specification remains subject to its publisher's terms. External runtime
libraries are imported only when their integration is selected and retain their
own licenses and notices. Before adding a runtime dependency, update this file
with its exact package name, version range, license, and source URL.

## Optional runtime libraries

UMP does not vendor or install these libraries automatically:

- `paho-mqtt` 2.1.0 (EPL-2.0 or EDL-1.0): optional VDA 5050 MQTT transport.
- `asyncua` 1.1.8 (LGPL-3.0-or-later): optional OPC UA client/server probe.
- `websockets` 15.0.1 (BSD-3-Clause): optional WebSocket probe.
- `rclpy` and `rmf_adapter`: optional ROS 2 and Open-RMF runtimes supplied by a
  compatible ROS installation.
- Eclipse Mosquitto 2.0.22 (EPL-2.0 or EDL-1.0): Docker MQTT broker.
- Shopify Toxiproxy 2.12.0 (MIT): Docker network-fault proxy.
- ROS 2 Jazzy and Gazebo Harmonic (primarily Apache-2.0): optional Linux visual
  and middleware runtime supplied by their published container/packages.

Deployers must review and retain the license notices for the exact versions they
install.
