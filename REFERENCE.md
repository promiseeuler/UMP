# UMP Standards and Implementation Reference

## Purpose

UMP is independently implemented. This project studies and interoperates with
established robotics standards so that it can reuse proven concepts and avoid
creating competing transports, status fields, or fleet interfaces without a
clear need.

Compatibility does not mean copying another project's source code. UMP uses
published specifications, documented interfaces, and properly licensed
dependencies. Third-party code, if introduced, must retain its license and
attribution and remain distinguishable from UMP-owned code.

## Related standards

### MassRobotics AMR Interoperability Standard

Reference: <https://github.com/MassRobotics-AMR/AMR_Interop_Standard>

Proven concepts relevant to UMP include manufacturer-neutral identity,
location, velocity, destination, availability, health, and task-state sharing.
UMP should map equivalent fields instead of inventing incompatible AMR terms.

MassRobotics primarily addresses autonomous mobile robots and shared
observation. UMP's intended gap is a semantic model spanning additional robot
classes plus high-level, externally planned collaboration assignments.

### Open-RMF

Reference: <https://www.open-rmf.org/>

Open-RMF provides multi-fleet task, traffic, and facility coordination. UMP
should integrate with it rather than reproduce building maps, traffic
negotiation, doors, lifts, or fleet scheduling. An Open-RMF bridge can translate
between UMP participant capabilities and RMF fleet adapters.

### VDA 5050

Reference: <https://www.vda.de/en/topics/automotive-industry/vda-5050>

VDA 5050 defines communication between a master control and mobile robots for
orders and state. UMP should provide a versioned VDA 5050 adapter for compatible
mobile-robot assignments and status while preserving VDA semantics.

### ROS 2 and DDS/RTPS

Reference: <https://docs.ros.org/en/rolling/Concepts/About-Internal-Interfaces.html>

ROS 2 already provides discovery, publish/subscribe, services, actions,
serialization, and multiple middleware implementations. UMP should use a ROS 2
binding where appropriate, not recreate ROS middleware. UMP supplies the common
semantic message profile and safety boundary above that transport.

### OPC UA for Robotics

Reference: <https://reference.opcfoundation.org/specs/OPC-40010-1/full>

OPC UA Robotics defines industrial robot information models for identification,
monitoring, operating data, and condition information. UMP should map industrial
telemetry and identity to these models instead of replacing plant-level OPC UA
infrastructure.

## UMP's focused responsibility

UMP owns only the cross-category semantic contract needed for a robot to say:

- who it is and which high-level capabilities it exposes;
- what it is doing, intends to do, and how far it has progressed;
- whether it is available, healthy, safe, and sufficiently powered;
- which high-level assignment it accepted, rejected, completed, or could not
  determine;
- which observations may be disclosed to each authenticated peer.

UMP does not own motor control, path planning, collision avoidance, emergency
stops, vendor autonomy, facility traffic management, or the reasoning model.

## Integration policy

1. Prefer an adapter or mapping over a competing protocol feature.
2. Preserve the external standard's units, identifiers, version, and lifecycle.
3. Keep third-party dependencies optional and outside the protocol model.
4. Add conformance fixtures for every supported mapping.
5. Record specification versions and licenses before merging an integration.
6. Describe UMP differentiation as an intended design scope, not an unsupported
   claim that no comparable implementation exists.

## Implemented compatibility

The reference implementation now includes loss-aware mapping profiles for all
five referenced ecosystems. The detailed field and authority matrix is in
[`docs/STANDARDS_INTEGRATIONS.md`](docs/STANDARDS_INTEGRATIONS.md). Unsupported
fields are reported explicitly and are not treated as evidence that the source
standard lacks the concept in every version or vendor extension.
