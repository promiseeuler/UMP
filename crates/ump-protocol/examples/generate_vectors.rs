use std::{fs, path::Path};

use prost::Message;
use ump_protocol::v1::{
    Advertisement, Capability, CapabilityAvailability, Endpoint, Envelope, Feature,
    HandoffProposal, IdempotencyPolicy, MachineDescriptor, OperationalState, Pose, Quaternion,
    SafetyState, SpatialContext, StateUpdate, TaskRequest, TelemetrySample, TransportKind, Vector3,
    envelope::Body,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let output = Path::new("tests/vectors");
    fs::create_dir_all(output)?;
    write(output.join("hello.bin"), hello())?;
    write(output.join("advertisement.bin"), advertisement())?;
    write(output.join("state.bin"), state())?;
    write(output.join("task-request.bin"), task_request())?;
    write(output.join("handoff-proposal.bin"), handoff_proposal())?;
    Ok(())
}

fn write(path: impl AsRef<Path>, envelope: Envelope) -> Result<(), std::io::Error> {
    let mut bytes = Vec::with_capacity(envelope.encoded_len());
    envelope
        .encode(&mut bytes)
        .expect("Vec encoding cannot fail");
    fs::write(path, bytes)
}

fn base(message_id: &str, body: Body) -> Envelope {
    Envelope {
        protocol_major: 1,
        protocol_minor: 0,
        message_id: message_id.into(),
        source_machine_id: "ump:machine:arm-vector".into(),
        source_session_id: "session-vector-1".into(),
        sent_at_ms: 1_000,
        expires_at_ms: Some(3_000),
        correlation_id: format!("correlation-{message_id}"),
        causation_id: String::new(),
        body: Some(body),
    }
}

fn task_request() -> Envelope {
    base(
        "vector-task-request-1",
        Body::TaskRequest(TaskRequest {
            task_id: "ump:task:delivery-vector-1".into(),
            capability: "org.ump.logistics.deliver".into(),
            input: br#"{"package":"package-1","destination":"zone-b"}"#.to_vec(),
            input_content_type: "application/json".into(),
            authority_lease_id: "ump:lease:delivery-vector-1".into(),
            idempotency_key: "delivery-vector-1".into(),
            idempotency_policy: IdempotencyPolicy::AtMostOnce.into(),
            deadline_ms: 2_500,
            maximum_attempts: 1,
        }),
    )
}

fn handoff_proposal() -> Envelope {
    base(
        "vector-handoff-proposal-1",
        Body::HandoffProposal(HandoffProposal {
            handoff_id: "ump:handoff:vector-1".into(),
            task_id: "ump:task:vector-1".into(),
            source_machine_id: "ump:machine:arm-vector".into(),
            destination_machine_id: "ump:machine:base-vector".into(),
            subject_id: "ump:subject:package-vector".into(),
            reservation_id: "ump:reservation:zone-vector".into(),
            transfer_context: Some(SpatialContext {
                reference_frame_id: "ump:frame:transfer-zone".into(),
                subject_frame_id: "ump:frame:package-vector".into(),
                pose: Some(Pose {
                    position_m: Some(Vector3 {
                        x: 1.25,
                        y: 0.5,
                        z: 0.8,
                    }),
                    orientation: Some(Quaternion {
                        x: 0.0,
                        y: 0.0,
                        z: 0.0,
                        w: 1.0,
                    }),
                }),
                source_time_ms: 1_000,
                maximum_age_ms: 500,
                position_uncertainty_m: 0.005,
                orientation_uncertainty_rad: 0.01,
            }),
            preconditions: vec!["destination_gripper_empty".into()],
            deadline_ms: 2_500,
        }),
    )
}

fn hello() -> Envelope {
    base(
        "vector-hello-1",
        Body::Hello(ump_protocol::v1::Hello {
            supported_major_versions: vec![1],
            minimum_minor_version: 0,
            maximum_minor_version: 0,
            presence_ttl_ms: 2_000,
        }),
    )
}

fn advertisement() -> Envelope {
    base(
        "vector-advertisement-1",
        Body::Advertisement(Advertisement {
            descriptor: Some(MachineDescriptor {
                machine_class: "robot_arm".into(),
                manufacturer: "UMP".into(),
                model: "vector-arm".into(),
                software_version: "0.1.0".into(),
                endpoints: vec![Endpoint {
                    uri: "quic://arm-vector.ump.local:7443".into(),
                    transport: TransportKind::Quic.into(),
                    priority: 1,
                }],
                features: vec![Feature {
                    name: "org.ump.feature.handoff".into(),
                    version: "1.0".into(),
                    required: false,
                }],
                capabilities: vec![Capability {
                    r#type: "org.ump.material.pick".into(),
                    version: "1.0".into(),
                    label: "Pick".into(),
                    input_schema_uri: "ump://schemas/material/pick-input/1".into(),
                    output_schema_uri: "ump://schemas/material/pick-output/1".into(),
                    availability: CapabilityAvailability::Available.into(),
                    observable: true,
                    invocable: true,
                    reservable: true,
                    interruptible: true,
                    handoff_capable: true,
                    revision: 7,
                    constraints: Vec::new(),
                }],
                revision: 3,
                deployment_mode: ump_protocol::v1::DeploymentMode::Direct.into(),
                proxy: None,
            }),
        }),
    )
}

fn state() -> Envelope {
    base(
        "vector-state-1",
        Body::StateUpdate(StateUpdate {
            operational: OperationalState::Idle.into(),
            safety: SafetyState::Normal.into(),
            health: Vec::new(),
            telemetry: vec![TelemetrySample {
                metric: "controller.temperature".into(),
                value: 41.5,
                unit: "Cel".into(),
                source_time_ms: 1_000,
                sequence: 9,
            }],
            revision: 4,
            source_time_ms: 1_000,
        }),
    )
}
