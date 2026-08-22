# Standards Integrations

UMP integrations translate established standards into the UMP 0.1 semantic
model. They do not merge those standards or take ownership of their native
responsibilities.

## Compatibility matrix

| Integration | Inbound awareness | Outbound awareness | External tasks | Native responsibility |
| --- | --- | --- | --- | --- |
| MassRobotics 1.0 | Identity, operation, task ID, location, health indicators, battery | Identity, operation, task ID, location, battery | Mapping contract available but read-only by default | AMR navigation and fleet behavior |
| VDA 5050 3.0 | Factsheet identity, state, pose, battery, safety, errors, order ID | Factsheet and state profile | Orders require explicit capability mapping and a valid UMP authority lease | AGV motion, route execution, and safety |
| Open-RMF | Fleet identity, participant state, level pose, task and progress | Participant identity, capabilities, state and pose | Task categories require explicit capability mapping and a valid UMP authority lease | Traffic, maps, doors, lifts, and scheduling |
| ROS 2 / UMP 0.1 interfaces | Semantic manifest and state topics | Semantic manifest and state topics | High-level `ExecuteCapability` action | ROS graph, vendor actions, autonomy, and safety |
| OPC UA Robotics 1.02 | Identification, operating mode, health, battery and semantic extension nodes | Filtered read-only UMP address space | Not exposed | Plant information model and robot controller |

## Installation model

All mapping code ships in the main UMP distribution. External libraries are
loaded only when their runtime is selected:

- VDA 5050 MQTT transport uses `paho-mqtt`.
- Open-RMF uses its supported ROS 2 installation with `rclpy` and `rmf_adapter`.
- ROS 2 uses `rclpy` and the separately built `ros2_interfaces/ump_interfaces` package.
- OPC UA uses `asyncua`.
- MassRobotics mapping has no required transport dependency; the robot owner
  supplies the approved WebSocket lifecycle.

Check a runtime without starting a robot node:

```sh
ump-integration runtime vda5050
ump-integration runtime open_rmf
ump-integration runtime ros2
ump-integration runtime opc_ua
```

Missing runtimes produce `IntegrationUnavailableError` only when selected.

## Configuration

Every integration uses `ump.integration-config/v1`. The config identifies the
standard version, endpoint, external-to-UMP identity binding, disclosure fields,
mode, and optional task mappings.

```json
{
  "profile": "ump.integration-config/v1",
  "type": "vda5050",
  "standard_version": "3.0.0",
  "endpoint": "mqtts://broker.example:8883",
  "mode": "task_enabled",
  "identity": {
    "external_id": "agv-17",
    "robot_id": "robot-mobile-17"
  },
  "task_mappings": [
    {
      "external_type": "transport",
      "capability": "ump.material.carry/v1",
      "input_fields": {"destination": "destination"}
    }
  ]
}
```

Validate before opening external or UMP network connections:

```sh
ump-integration validate integration.json
ump-node --integration-config integration.json <other node arguments>
```

`read_only` is the default. `task_enabled` alone does not authorize work: the
target adapter must advertise the mapped capability and the robot owner must
grant a matching UMP authority lease.

## Mapping evidence

Every conversion returns `ump-mapping-report-v1.schema.json` evidence listing
mapped, defaulted, unsupported, and rejected fields. Missing data remains
unknown. Adapters must not fabricate intent, progress, safety, pose, or battery.

Run dependency-free mapping fixtures:

```sh
python3 examples/standards_mapping_demo.py massrobotics
python3 examples/standards_mapping_demo.py vda5050
python3 examples/standards_mapping_demo.py open_rmf
python3 examples/standards_mapping_demo.py ros2
python3 examples/standards_mapping_demo.py opc_ua
```

These fixtures validate conversion behavior. They do not validate a broker,
ROS graph, Open-RMF deployment, OPC UA server, vendor controller, or physical
robot.
