use serde::Serialize;
use ump_runtime::task::{
    Lease, LeaseStatus, MemoryJournal, NewTask, RetryPolicy, TaskEngine, TaskError, TaskRecord,
    TaskState,
};

const EXECUTOR: &str = "ump:machine:delivery-robot";
const COORDINATOR: &str = "ump:machine:coordinator";
const CAPABILITY: &str = "org.ump.logistics.deliver";

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
    physical_action_surrogates: u32,
    duplicate_physical_actions: u32,
    unauthorized_executions: u32,
    correlated_trace_events: usize,
    passed: bool,
    checks: Vec<Check>,
}

fn lease(id: &str, holder: &str, expires_at_ms: u64) -> Lease {
    Lease {
        lease_id: id.into(),
        grantor_machine_id: EXECUTOR.into(),
        holder_machine_id: holder.into(),
        allowed_capabilities: vec![CAPABILITY.into()],
        resource_ids: vec!["ump:resource:delivery-zone".into()],
        issued_at_ms: 0,
        expires_at_ms,
        maximum_clock_uncertainty_ms: 10,
        renewable: true,
        exclusive: true,
        status: LeaseStatus::Active,
        revision: 1,
    }
}

fn request(id: &str, lease_id: &str, policy: RetryPolicy) -> NewTask {
    NewTask {
        task_id: id.into(),
        issuer_machine_id: COORDINATOR.into(),
        capability: CAPABILITY.into(),
        input: format!(r#"{{"package":"{id}","destination":"zone-b"}}"#).into_bytes(),
        input_content_type: "application/json".into(),
        authority_lease_id: lease_id.into(),
        idempotency_key: format!("delivery-{id}"),
        retry_policy: policy,
        deadline_ms: 9_000,
        maximum_attempts: if policy == RetryPolicy::SafeToRetry {
            2
        } else {
            1
        },
        correlation_id: format!("trace-{id}"),
        causation_id: format!("request-message-{id}"),
    }
}

fn engine(lease_id: &str) -> TaskEngine<MemoryJournal> {
    let mut engine = TaskEngine::open(EXECUTOR, MemoryJournal::default()).unwrap();
    engine.register_capability(CAPABILITY, true).unwrap();
    engine
        .grant_lease(lease(lease_id, COORDINATOR, 10_000), 1)
        .unwrap();
    engine
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
    let mut physical_actions = 0;
    let mut duplicate_actions = 0;
    let mut unauthorized_executions = 0;
    let mut trace_events = 0;

    // Baseline: lease, accept, progress, and success.
    let mut baseline = engine("lease-baseline");
    baseline
        .submit(
            request("baseline", "lease-baseline", RetryPolicy::AtMostOnce),
            2,
        )
        .unwrap();
    baseline.start("baseline", 3).unwrap();
    physical_actions += 1;
    baseline.progress("baseline", 500, "in_transit", 4).unwrap();
    let completed = baseline
        .finish(
            "baseline",
            TaskState::Succeeded,
            b"delivered".to_vec(),
            "",
            5,
        )
        .unwrap();
    trace_events += baseline.trace("baseline").unwrap().len();
    checks.push(check(
        "authorized delivery completes",
        completed.state == TaskState::Succeeded,
        "lease-backed task moves through accepted, running, progress, and succeeded",
    ));

    // Cancellation before start and while running.
    let mut cancellation = engine("lease-cancel");
    cancellation
        .submit(
            request("cancel-before", "lease-cancel", RetryPolicy::AtMostOnce),
            2,
        )
        .unwrap();
    let before = cancellation
        .cancel("cancel-before", COORDINATOR, 3)
        .unwrap();
    let prevented = cancellation.start("cancel-before", 4).is_err();
    cancellation
        .submit(
            request("cancel-during", "lease-cancel", RetryPolicy::AtMostOnce),
            5,
        )
        .unwrap();
    cancellation.start("cancel-during", 6).unwrap();
    physical_actions += 1;
    let pending = cancellation
        .cancel("cancel-during", COORDINATOR, 7)
        .unwrap();
    let during = cancellation
        .finish("cancel-during", TaskState::Cancelled, Vec::new(), "", 8)
        .unwrap();
    checks.push(check(
        "cancellation is phase-aware",
        before.state == TaskState::Cancelled
            && prevented
            && pending.state == TaskState::CancelPending
            && during.state == TaskState::Cancelled,
        "pre-start cancellation prevents invocation; running work enters cancel_pending",
    ));

    // Duplicate network delivery cannot duplicate the action surrogate.
    let mut duplicate = engine("lease-duplicate");
    let duplicate_request = request("duplicate", "lease-duplicate", RetryPolicy::AtMostOnce);
    let first = duplicate.submit(duplicate_request.clone(), 2).unwrap();
    let replay = duplicate.submit(duplicate_request, 3).unwrap();
    duplicate.start("duplicate", 4).unwrap();
    physical_actions += 1;
    if duplicate.start("duplicate", 5).is_ok() {
        duplicate_actions += 1;
    }
    checks.push(check(
        "duplicate task delivery is idempotent",
        first == replay && duplicate_actions == 0,
        "duplicate returns the durable record and a second execution permit is denied",
    ));

    // Retry-safe work observes backoff and the configured attempt bound.
    let mut retry = engine("lease-retry");
    retry
        .submit(request("retry", "lease-retry", RetryPolicy::SafeToRetry), 2)
        .unwrap();
    retry.start("retry", 3).unwrap();
    physical_actions += 1;
    let pending = retry
        .fail_attempt("retry", "temporary_network", true, 100, 4)
        .unwrap();
    let early_retry_denied = retry.start("retry", 103).is_err();
    retry.start("retry", 104).unwrap();
    physical_actions += 1;
    let retry_result = retry
        .finish(
            "retry",
            TaskState::Succeeded,
            b"delivered".to_vec(),
            "",
            105,
        )
        .unwrap();
    checks.push(check(
        "retry-safe failure uses bounded backoff",
        pending.state == TaskState::RetryPending
            && early_retry_denied
            && retry_result.state == TaskState::Succeeded
            && retry_result.attempts_started == 2,
        "second permit is unavailable before backoff and succeeds within attempt bound",
    ));

    // Expired command.
    let mut expired = engine("lease-expired-command");
    let expired_result = expired.submit(
        request(
            "expired-command",
            "lease-expired-command",
            RetryPolicy::AtMostOnce,
        ),
        9_000,
    );
    checks.push(check(
        "expired command is rejected",
        matches!(expired_result, Err(TaskError::Invalid(_))),
        "deadline is checked before acceptance",
    ));

    // Crash after execution permission: durable recovery becomes unknown, never a retry.
    let mut crashing = engine("lease-crash");
    crashing
        .submit(request("crash", "lease-crash", RetryPolicy::AtMostOnce), 2)
        .unwrap();
    crashing.start("crash", 3).unwrap();
    physical_actions += 1;
    let journal = crashing.into_journal();
    let mut restarted = TaskEngine::open(EXECUTOR, journal).unwrap();
    let recovered = restarted.recover_incomplete(4).unwrap();
    let crash_record = restarted.tasks()["crash"].clone();
    let restart_denied = restarted.start("crash", 5).is_err();
    checks.push(check(
        "crash after acceptance requires reconciliation",
        recovered == ["crash"]
            && crash_record.state == TaskState::Unknown
            && crash_record.inspection_required
            && restart_denied,
        "durable running record recovers as unknown and receives no second permit",
    ));

    // Partition: executor completes locally; issuer converges from snapshot after reconnect.
    let mut partition = engine("lease-partition");
    partition
        .submit(
            request("partition", "lease-partition", RetryPolicy::AtMostOnce),
            2,
        )
        .unwrap();
    let coordinator_journal = partition.journal().clone();
    partition.start("partition", 3).unwrap();
    physical_actions += 1;
    partition.progress("partition", 800, "arrived", 4).unwrap();
    let executor_snapshot = partition
        .finish(
            "partition",
            TaskState::Succeeded,
            b"delivered".to_vec(),
            "",
            5,
        )
        .unwrap();
    let mut coordinator_view = TaskEngine::open(EXECUTOR, coordinator_journal).unwrap();
    let coordinator_snapshot: TaskRecord = coordinator_view
        .reconcile(&executor_snapshot, 6, EXECUTOR)
        .unwrap();
    checks.push(check(
        "partition reconciles terminal outcome",
        coordinator_snapshot.state == executor_snapshot.state
            && coordinator_snapshot.revision == executor_snapshot.revision,
        "issuer adopts authenticated executor snapshot after reconnect",
    ));

    // Lease expiry and explicit revocation both stop authority.
    let mut lease_loss = engine("lease-loss");
    lease_loss
        .submit(
            request("lease-expiry", "lease-loss", RetryPolicy::AtMostOnce),
            2,
        )
        .unwrap();
    lease_loss.start("lease-expiry", 3).unwrap();
    physical_actions += 1;
    let affected = lease_loss.expire_leases(10_000).unwrap();
    let expiry_unknown = lease_loss.tasks()["lease-expiry"].state == TaskState::Unknown;
    let mut revoked = engine("lease-revoked");
    revoked
        .submit(
            request("revoked", "lease-revoked", RetryPolicy::AtMostOnce),
            2,
        )
        .unwrap();
    revoked
        .revoke_lease("lease-revoked", EXECUTOR, 1, 3)
        .unwrap();
    let revoked_prevented = revoked.start("revoked", 4).is_err();
    checks.push(check(
        "lease expiry and revocation stop authority",
        affected == ["lease-expiry"] && expiry_unknown && revoked_prevented,
        "running work becomes unknown; accepted work is cancelled before invocation",
    ));

    // Coordinator restart uses executor's durable terminal snapshot.
    let coordinator_journal = coordinator_view.into_journal();
    let coordinator_after_restart = TaskEngine::open(EXECUTOR, coordinator_journal)
        .unwrap()
        .tasks()["partition"]
        .clone();
    checks.push(check(
        "coordinator restart converges",
        coordinator_after_restart.state == TaskState::Succeeded,
        "restarted coordinator reconstructs terminal state from executor reconciliation",
    ));

    // Conflicting issuer lacks the holder identity named by the lease.
    let mut conflict = engine("lease-conflict");
    let mut unauthorized = request("conflict", "lease-conflict", RetryPolicy::AtMostOnce);
    unauthorized.issuer_machine_id = "ump:machine:competing-coordinator".into();
    if conflict.submit(unauthorized, 2).is_ok() {
        unauthorized_executions += 1;
    }
    checks.push(check(
        "conflicting issuer is denied",
        unauthorized_executions == 0,
        "authenticated issuer does not match lease holder",
    ));

    // Non-idempotent uncertain outcome cannot be retried.
    let mut non_idempotent = engine("lease-non-idempotent");
    non_idempotent
        .submit(
            request(
                "non-idempotent",
                "lease-non-idempotent",
                RetryPolicy::ReconcileRequired,
            ),
            2,
        )
        .unwrap();
    non_idempotent.start("non-idempotent", 3).unwrap();
    physical_actions += 1;
    let journal = non_idempotent.into_journal();
    let mut uncertain = TaskEngine::open(EXECUTOR, journal).unwrap();
    uncertain.recover_incomplete(4).unwrap();
    let unknown = &uncertain.tasks()["non-idempotent"];
    checks.push(check(
        "non-idempotent unknown outcome is never blindly retried",
        unknown.state == TaskState::Unknown
            && unknown.inspection_required
            && uncertain.start("non-idempotent", 5).is_err(),
        "outcome remains explicit unknown/reconcile with inspection required",
    ));

    trace_events += restarted.trace("crash").unwrap().len();
    trace_events += partition.trace("partition").unwrap().len();
    let traces_correlated = [
        baseline.trace("baseline").unwrap(),
        restarted.trace("crash").unwrap(),
        partition.trace("partition").unwrap(),
    ]
    .iter()
    .flatten()
    .all(|event| !event.correlation_id.is_empty());
    checks.push(check(
        "all task transitions are correlated",
        traces_correlated,
        "audit events retain task correlation IDs across progress and restart",
    ));

    let passed = checks.iter().all(|check| check.passed)
        && duplicate_actions == 0
        && unauthorized_executions == 0;
    let report = Report {
        scenario: "S2 delivery task under faults",
        variants: 13,
        physical_action_surrogates: physical_actions,
        duplicate_physical_actions: duplicate_actions,
        unauthorized_executions,
        correlated_trace_events: trace_events,
        passed,
        checks,
    };
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
    if !passed {
        std::process::exit(1);
    }
}
