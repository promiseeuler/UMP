use prost::Message;
use ump_protocol::v1::{Envelope, OperationalState, SafetyState, TransportKind, envelope::Body};

fn decode(bytes: &[u8]) -> Envelope {
    Envelope::decode(bytes).expect("checked-in vector must decode")
}

#[test]
fn rust_decodes_and_round_trips_golden_vectors() {
    let vectors = [
        include_bytes!("../../../tests/vectors/hello.bin").as_slice(),
        include_bytes!("../../../tests/vectors/advertisement.bin").as_slice(),
        include_bytes!("../../../tests/vectors/state.bin").as_slice(),
        include_bytes!("../../../tests/vectors/task-request.bin").as_slice(),
        include_bytes!("../../../tests/vectors/handoff-proposal.bin").as_slice(),
    ];
    for bytes in vectors {
        let envelope = decode(bytes);
        assert_eq!(envelope.encode_to_vec(), bytes);
        assert_eq!(envelope.protocol_major, 1);
        assert_eq!(envelope.source_machine_id, "ump:machine:arm-vector");
    }
}

#[test]
fn vector_semantics_match_the_specification() {
    let advertisement = decode(include_bytes!("../../../tests/vectors/advertisement.bin"));
    let Some(Body::Advertisement(advertisement)) = advertisement.body else {
        panic!("expected advertisement vector")
    };
    let descriptor = advertisement.descriptor.expect("descriptor");
    assert_eq!(descriptor.machine_class, "robot_arm");
    assert_eq!(
        descriptor.endpoints[0].transport,
        TransportKind::Quic as i32
    );
    assert_eq!(descriptor.capabilities[0].revision, 7);

    let state = decode(include_bytes!("../../../tests/vectors/state.bin"));
    let Some(Body::StateUpdate(state)) = state.body else {
        panic!("expected state vector")
    };
    assert_eq!(state.operational, OperationalState::Idle as i32);
    assert_eq!(state.safety, SafetyState::Normal as i32);
    assert_eq!(state.telemetry[0].value, 41.5);

    let task = decode(include_bytes!("../../../tests/vectors/task-request.bin"));
    assert_eq!(task.correlation_id, "correlation-vector-task-request-1");
    let Some(Body::TaskRequest(task)) = task.body else {
        panic!("expected task request vector")
    };
    assert_eq!(task.task_id, "ump:task:delivery-vector-1");
    assert_eq!(task.maximum_attempts, 1);

    let handoff = decode(include_bytes!(
        "../../../tests/vectors/handoff-proposal.bin"
    ));
    let Some(Body::HandoffProposal(handoff)) = handoff.body else {
        panic!("expected handoff proposal vector")
    };
    assert_eq!(handoff.handoff_id, "ump:handoff:vector-1");
    let context = handoff.transfer_context.expect("spatial context");
    assert_eq!(context.reference_frame_id, "ump:frame:transfer-zone");
    assert_eq!(context.pose.unwrap().position_m.unwrap().x, 1.25);
}

#[test]
fn randomized_malformed_envelopes_never_panic() {
    let mut state = 0x4d595df4d0f33173_u64;
    for length in 0..2_048 {
        let mut bytes = vec![0_u8; length % 1_025];
        for byte in &mut bytes {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            *byte = state as u8;
        }
        let _ = Envelope::decode(bytes.as_slice());
    }
}
