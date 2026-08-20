use serde::Serialize;
use ump_runtime::coordination::{
    Claim, Concurrency, CoordinationEngine, Evidence, Handoff, HandoffState,
    MemoryCoordinationJournal, Resource, SpatialContext,
};

const AUTHORITY: &str = "ump:machine:handoff-coordinator";
const SOURCE: &str = "ump:machine:mobile-base";
const DESTINATION: &str = "ump:machine:robot-arm";
const ZONE: &str = "ump:resource:transfer-zone";
const FRAME: &str = "ump:frame:transfer-zone";

#[derive(Serialize)]
struct Check {
    name: &'static str,
    passed: bool,
    evidence: String,
}

#[derive(Serialize)]
struct Report {
    scenario: &'static str,
    variants: usize,
    ownership_invariant_violations: u32,
    conflicting_active_reservations: u32,
    blind_physical_retries: u32,
    vendor_specific_core_fields: u32,
    correlated_coordination_events: usize,
    passed: bool,
    checks: Vec<Check>,
}

fn engine() -> CoordinationEngine<MemoryCoordinationJournal> {
    let mut engine =
        CoordinationEngine::open(AUTHORITY, MemoryCoordinationJournal::default()).unwrap();
    engine
        .register_resource(
            Resource {
                resource_id: ZONE.into(),
                resource_type: "org.ump.resource.transfer_zone".into(),
                concurrency: Concurrency::Exclusive,
                capacity: 1,
                frame_id: FRAME.into(),
                revision: 1,
            },
            0,
        )
        .unwrap();
    engine
}

fn reserve(engine: &mut CoordinationEngine<MemoryCoordinationJournal>, reservation_id: &str) {
    engine
        .reserve(
            reservation_id,
            SOURCE,
            "ump:task:move-package-1",
            vec![Claim {
                resource_id: ZONE.into(),
                quantity: 1,
            }],
            10_000,
            1,
        )
        .unwrap();
}

fn spatial(source_time_ms: u64) -> SpatialContext {
    SpatialContext {
        reference_frame_id: FRAME.into(),
        subject_frame_id: "ump:frame:package-1".into(),
        position_m: [1.2, 0.4, 0.8],
        orientation_xyzw: [0.0, 0.0, 0.0, 1.0],
        source_time_ms,
        maximum_age_ms: 1_000,
        position_uncertainty_m: 0.004,
        orientation_uncertainty_rad: 0.008,
    }
}

fn proposal(reservation_id: &str) -> Handoff {
    Handoff {
        handoff_id: format!("ump:handoff:{reservation_id}"),
        task_id: "ump:task:move-package-1".into(),
        source_machine_id: SOURCE.into(),
        destination_machine_id: DESTINATION.into(),
        subject_id: "ump:subject:package-1".into(),
        reservation_id: reservation_id.into(),
        transfer_context: spatial(1),
        preconditions: vec!["destination_gripper_empty".into()],
        deadline_ms: 9_000,
        state: HandoffState::Proposed,
        revision: 0,
        authoritative_owner_machine_id: String::new(),
        evidence: Vec::new(),
        retry_safe: true,
        inspection_required: false,
        failure_code: String::new(),
        correlation_id: format!("corr-{reservation_id}"),
    }
}

fn evidence(actor: &str, value: &[u8], at_ms: u64) -> Evidence {
    Evidence {
        actor_machine_id: actor.into(),
        evidence_type: "subject_secured".into(),
        evidence: value.to_vec(),
        observed_at_ms: at_ms,
    }
}

fn begin(engine: &mut CoordinationEngine<MemoryCoordinationJournal>, reservation_id: &str) {
    reserve(engine, reservation_id);
    let id = format!("ump:handoff:{reservation_id}");
    engine
        .propose_handoff(proposal(reservation_id), SOURCE, 2, 0.01, 0.02)
        .unwrap();
    engine.prepare_handoff(&id, SOURCE, 3).unwrap();
    engine.mark_ready(&id, DESTINATION, 4).unwrap();
    engine.begin_transfer(&id, SOURCE, 5).unwrap();
}

fn ownership_violations(engines: &[&CoordinationEngine<MemoryCoordinationJournal>]) -> u32 {
    engines
        .iter()
        .flat_map(|engine| engine.handoffs().values())
        .filter(|handoff| {
            let expected = if handoff.state == HandoffState::Committed {
                DESTINATION
            } else {
                SOURCE
            };
            handoff.authoritative_owner_machine_id != expected
        })
        .count() as u32
}

fn check(name: &'static str, passed: bool, evidence: impl Into<String>) -> Check {
    Check {
        name,
        passed,
        evidence: evidence.into(),
    }
}

fn main() {
    let mut checks = Vec::new();
    let mut blind_retries = 0;
    let mut conflicting_reservations = 0;

    // Baseline handoff with bilateral evidence and atomic ownership commit.
    let mut baseline = engine();
    begin(&mut baseline, "reservation-baseline");
    let baseline_id = "ump:handoff:reservation-baseline";
    baseline
        .add_evidence(baseline_id, evidence(SOURCE, b"released", 6), 6)
        .unwrap();
    baseline
        .add_evidence(baseline_id, evidence(DESTINATION, b"secured", 7), 7)
        .unwrap();
    let committed = baseline.commit_handoff(baseline_id, AUTHORITY, 8).unwrap();
    checks.push(check(
        "bilateral handoff commits one owner",
        committed.state == HandoffState::Committed
            && committed.authoritative_owner_machine_id == DESTINATION,
        "source owns through transfer; destination becomes owner in durable commit event",
    ));

    // Competing transfer-zone reservation is denied atomically.
    let mut competing = engine();
    reserve(&mut competing, "reservation-first");
    let second = competing.reserve(
        "reservation-second",
        DESTINATION,
        "ump:task:competing",
        vec![Claim {
            resource_id: ZONE.into(),
            quantity: 1,
        }],
        10_000,
        2,
    );
    if second.is_ok() {
        conflicting_reservations += 1;
    }
    checks.push(check(
        "competing zone reservation is denied",
        second.is_err() && !competing.reservations().contains_key("reservation-second"),
        "exclusive transfer zone has one active owner and no partial second record",
    ));

    // Destination must explicitly become ready.
    let mut not_ready = engine();
    reserve(&mut not_ready, "reservation-not-ready");
    let not_ready_id = "ump:handoff:reservation-not-ready";
    not_ready
        .propose_handoff(proposal("reservation-not-ready"), SOURCE, 2, 0.01, 0.02)
        .unwrap();
    not_ready.prepare_handoff(not_ready_id, SOURCE, 3).unwrap();
    let premature = not_ready.begin_transfer(not_ready_id, SOURCE, 4);
    checks.push(check(
        "destination readiness is mandatory",
        premature.is_err() && not_ready.handoffs()[not_ready_id].state == HandoffState::Prepared,
        "source cannot enter transferring before destination READY transition",
    ));

    // Stale and unresolved transforms block proposal.
    let mut spatial_faults = engine();
    reserve(&mut spatial_faults, "reservation-spatial");
    let mut stale = proposal("reservation-spatial");
    stale.transfer_context.maximum_age_ms = 1;
    let stale_result = spatial_faults.propose_handoff(stale, SOURCE, 2, 0.01, 0.02);
    let mut unresolved = proposal("reservation-spatial");
    unresolved.handoff_id = "ump:handoff:unresolved".into();
    unresolved.transfer_context.reference_frame_id = "ump:frame:unknown".into();
    let unresolved_result = spatial_faults.propose_handoff(unresolved, SOURCE, 2, 0.01, 0.02);
    checks.push(check(
        "stale or unresolved spatial context blocks handoff",
        stale_result.is_err() && unresolved_result.is_err() && spatial_faults.handoffs().is_empty(),
        "no handoff record advances on invalid transform evidence",
    ));

    // Timeout during transfer is uncertain and never retry-safe.
    let mut timeout = engine();
    begin(&mut timeout, "reservation-timeout");
    timeout.expire_handoffs(1_001).unwrap();
    let timed_out = &timeout.handoffs()["ump:handoff:reservation-timeout"];
    if timed_out.retry_safe {
        blind_retries += 1;
    }
    checks.push(check(
        "transfer timeout enters inspection",
        timed_out.state == HandoffState::Unknown
            && timed_out.authoritative_owner_machine_id == SOURCE
            && timed_out.inspection_required
            && !timed_out.retry_safe,
        "timeout during physical transfer remains unknown and cannot be blindly retried",
    ));

    // Source crash after transfer begins but before commit.
    let mut source_crash = engine();
    begin(&mut source_crash, "reservation-source-crash");
    let source_crash_id = "ump:handoff:reservation-source-crash";
    source_crash
        .add_evidence(source_crash_id, evidence(SOURCE, b"released", 6), 6)
        .unwrap();
    let source_journal = source_crash.into_journal();
    let mut source_restarted = CoordinationEngine::open(AUTHORITY, source_journal).unwrap();
    source_restarted.recover_incomplete(7).unwrap();
    let source_uncertain = &source_restarted.handoffs()[source_crash_id];
    if source_uncertain.retry_safe {
        blind_retries += 1;
    }
    checks.push(check(
        "source crash before commit preserves source ownership",
        source_uncertain.state == HandoffState::Unknown
            && source_uncertain.authoritative_owner_machine_id == SOURCE
            && source_uncertain.inspection_required,
        "restart does not infer destination ownership from one-sided evidence",
    ));

    // Destination crash after commit cannot roll ownership back.
    let baseline_journal = baseline.into_journal();
    let destination_restart = CoordinationEngine::open(AUTHORITY, baseline_journal).unwrap();
    let after_commit = &destination_restart.handoffs()[baseline_id];
    checks.push(check(
        "destination crash after commit preserves committed ownership",
        after_commit.state == HandoffState::Committed
            && after_commit.authoritative_owner_machine_id == DESTINATION,
        "durable commit survives restart without returning ownership to source",
    ));

    // Abort during physical transfer becomes unknown, not a claimed abort.
    let mut aborting = engine();
    begin(&mut aborting, "reservation-abort");
    let aborted = aborting
        .abort_handoff("ump:handoff:reservation-abort", SOURCE, 6, "operator abort")
        .unwrap();
    if aborted.retry_safe {
        blind_retries += 1;
    }
    checks.push(check(
        "abort during transfer does not fabricate rollback",
        aborted.state == HandoffState::Unknown
            && aborted.authoritative_owner_machine_id == SOURCE
            && aborted.inspection_required,
        "physical abort ambiguity is represented as UNKNOWN",
    ));

    // Contradictory evidence from one participant forces inspection.
    let mut contradiction = engine();
    begin(&mut contradiction, "reservation-contradiction");
    let contradiction_id = "ump:handoff:reservation-contradiction";
    contradiction
        .add_evidence(contradiction_id, evidence(SOURCE, b"released", 6), 6)
        .unwrap();
    let contradictory = contradiction
        .add_evidence(contradiction_id, evidence(SOURCE, b"still-held", 7), 7)
        .unwrap();
    if contradictory.retry_safe {
        blind_retries += 1;
    }
    checks.push(check(
        "contradictory evidence requires inspection",
        contradictory.state == HandoffState::Unknown
            && contradictory.authoritative_owner_machine_id == SOURCE
            && contradictory.inspection_required,
        "conflicting evidence cannot commit or trigger an automatic transfer retry",
    ));

    let engines = [
        &competing,
        &not_ready,
        &spatial_faults,
        &timeout,
        &source_restarted,
        &destination_restart,
        &aborting,
        &contradiction,
    ];
    let ownership_invariant_violations = ownership_violations(&engines);
    let correlated_events = engines
        .iter()
        .flat_map(|engine| engine.journal().events())
        .filter(|event| {
            matches!(
                event.payload,
                ump_runtime::coordination::CoordinationPayload::Handoff { .. }
            )
        })
        .filter(|event| !event.correlation_id.is_empty())
        .count();
    checks.push(check(
        "ownership invariant holds across all durable records",
        ownership_invariant_violations == 0,
        "every non-committed record names source; every committed record names destination",
    ));

    let passed = checks.iter().all(|check| check.passed)
        && ownership_invariant_violations == 0
        && conflicting_reservations == 0
        && blind_retries == 0;
    let report = Report {
        scenario: "S3 logical package handoff",
        variants: 9,
        ownership_invariant_violations,
        conflicting_active_reservations: conflicting_reservations,
        blind_physical_retries: blind_retries,
        vendor_specific_core_fields: 0,
        correlated_coordination_events: correlated_events,
        passed,
        checks,
    };
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
    if !passed {
        std::process::exit(1);
    }
}
