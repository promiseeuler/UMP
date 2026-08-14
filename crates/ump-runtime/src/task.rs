use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

pub const MAX_TASK_INPUT_BYTES: usize = 64 * 1024;
pub const MAX_TASKS: usize = 10_000;
pub const MAX_IDENTIFIER_BYTES: usize = 256;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskState {
    Requested,
    Accepted,
    Running,
    CancelPending,
    Succeeded,
    Failed,
    Cancelled,
    Rejected,
    Unknown,
    RetryPending,
}

impl TaskState {
    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::Succeeded | Self::Failed | Self::Cancelled | Self::Rejected
        )
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RetryPolicy {
    SafeToRetry,
    AtMostOnce,
    ReconcileRequired,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LeaseStatus {
    Active,
    Revoked,
    Expired,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Lease {
    pub lease_id: String,
    pub grantor_machine_id: String,
    pub holder_machine_id: String,
    pub allowed_capabilities: Vec<String>,
    pub resource_ids: Vec<String>,
    pub issued_at_ms: u64,
    pub expires_at_ms: u64,
    pub maximum_clock_uncertainty_ms: u64,
    pub renewable: bool,
    pub exclusive: bool,
    pub status: LeaseStatus,
    pub revision: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NewTask {
    pub task_id: String,
    pub issuer_machine_id: String,
    pub capability: String,
    pub input: Vec<u8>,
    pub input_content_type: String,
    pub authority_lease_id: String,
    pub idempotency_key: String,
    pub retry_policy: RetryPolicy,
    pub deadline_ms: u64,
    pub maximum_attempts: u32,
    pub correlation_id: String,
    pub causation_id: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TaskRecord {
    pub task_id: String,
    pub issuer_machine_id: String,
    pub executor_machine_id: String,
    pub capability: String,
    pub input_sha256: String,
    pub input: Vec<u8>,
    pub input_content_type: String,
    pub authority_lease_id: String,
    pub idempotency_key: String,
    pub retry_policy: RetryPolicy,
    pub deadline_ms: u64,
    pub maximum_attempts: u32,
    pub attempts_started: u32,
    pub next_attempt_at_ms: u64,
    pub interruptible: bool,
    pub state: TaskState,
    pub revision: u64,
    pub inspection_required: bool,
    pub result: Vec<u8>,
    pub error_code: String,
    pub correlation_id: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum JournalPayload {
    Task { snapshot: TaskRecord },
    Lease { snapshot: Lease },
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AuditEvent {
    pub sequence: u64,
    pub at_ms: u64,
    pub actor_machine_id: String,
    pub correlation_id: String,
    pub causation_id: String,
    pub previous_state: Option<String>,
    pub next_state: String,
    pub reason: String,
    pub payload: JournalPayload,
}

pub trait Journal {
    fn load(&self) -> Result<Vec<AuditEvent>, TaskError>;
    fn append(&mut self, event: &AuditEvent) -> Result<(), TaskError>;
}

#[derive(Clone, Debug, Default)]
pub struct MemoryJournal {
    events: Vec<AuditEvent>,
}

impl MemoryJournal {
    pub fn events(&self) -> &[AuditEvent] {
        &self.events
    }
}

impl Journal for MemoryJournal {
    fn load(&self) -> Result<Vec<AuditEvent>, TaskError> {
        Ok(self.events.clone())
    }

    fn append(&mut self, event: &AuditEvent) -> Result<(), TaskError> {
        self.events.push(event.clone());
        Ok(())
    }
}

#[derive(Debug)]
pub struct FileJournal {
    path: PathBuf,
}

impl FileJournal {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Journal for FileJournal {
    fn load(&self) -> Result<Vec<AuditEvent>, TaskError> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let file = fs::File::open(&self.path).map_err(TaskError::journal)?;
        BufReader::new(file)
            .lines()
            .map(|line| {
                let line = line.map_err(TaskError::journal)?;
                serde_json::from_str(&line).map_err(TaskError::journal)
            })
            .collect()
    }

    fn append(&mut self, event: &AuditEvent) -> Result<(), TaskError> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(TaskError::journal)?;
        }
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(TaskError::journal)?;
        serde_json::to_writer(&mut file, event).map_err(TaskError::journal)?;
        file.write_all(b"\n").map_err(TaskError::journal)?;
        file.sync_data().map_err(TaskError::journal)
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum TaskError {
    #[error("invalid task or lease: {0}")]
    Invalid(&'static str),
    #[error("task {0} was not found")]
    UnknownTask(String),
    #[error("lease {0} was not found")]
    UnknownLease(String),
    #[error("authority denied: {0}")]
    AuthorityDenied(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("invalid transition for task {task_id}: {from:?} -> {to:?}")]
    InvalidTransition {
        task_id: String,
        from: TaskState,
        to: TaskState,
    },
    #[error("journal failure: {0}")]
    Journal(String),
}

impl TaskError {
    fn journal(error: impl std::fmt::Display) -> Self {
        Self::Journal(error.to_string())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionPermit {
    pub task_id: String,
    pub capability: String,
    pub attempt: u32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapabilityPolicy {
    pub capability: String,
    pub interruptible: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TaskContext {
    pub task_id: String,
    pub issuer_machine_id: String,
    pub capability: String,
    pub input: Vec<u8>,
    pub input_content_type: String,
    pub attempt: u32,
    pub deadline_ms: u64,
    pub correlation_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HandlerFailure {
    pub code: String,
    pub detail: String,
    pub outcome_unknown: bool,
    pub retryable: bool,
    pub retry_after_ms: u64,
}

pub trait TaskHandler: Send + Sync {
    fn capability(&self) -> &str;
    fn interruptible(&self) -> bool;
    fn execute(&self, context: &TaskContext) -> Result<Vec<u8>, HandlerFailure>;
}

#[derive(Default)]
pub struct HandlerRegistry {
    handlers: BTreeMap<String, Box<dyn TaskHandler>>,
}

impl HandlerRegistry {
    pub fn register(&mut self, handler: impl TaskHandler + 'static) -> Result<(), TaskError> {
        let capability = handler.capability().to_owned();
        if capability.is_empty() || capability.len() > MAX_IDENTIFIER_BYTES {
            return Err(TaskError::Invalid("handler capability is invalid"));
        }
        if self
            .handlers
            .insert(capability.clone(), Box::new(handler))
            .is_some()
        {
            return Err(TaskError::Conflict(format!(
                "handler already registered for {capability}"
            )));
        }
        Ok(())
    }

    pub fn contains(&self, capability: &str) -> bool {
        self.handlers.contains_key(capability)
    }

    pub fn execute<J: Journal>(
        &self,
        engine: &mut TaskEngine<J>,
        task_id: &str,
        now_ms: u64,
    ) -> Result<TaskRecord, TaskError> {
        let before = engine.task(task_id)?.clone();
        let handler = self.handlers.get(&before.capability).ok_or_else(|| {
            TaskError::AuthorityDenied(format!(
                "no adapter handler registered for {}",
                before.capability
            ))
        })?;
        if handler.interruptible() != before.interruptible {
            return Err(TaskError::Conflict(
                "handler interruptibility differs from accepted task".into(),
            ));
        }
        let permit = engine.start(task_id, now_ms)?;
        let running = engine.task(task_id)?.clone();
        let context = TaskContext {
            task_id: running.task_id.clone(),
            issuer_machine_id: running.issuer_machine_id.clone(),
            capability: running.capability.clone(),
            input: running.input.clone(),
            input_content_type: running.input_content_type.clone(),
            attempt: permit.attempt,
            deadline_ms: running.deadline_ms,
            correlation_id: running.correlation_id.clone(),
        };
        match handler.execute(&context) {
            Ok(output) => engine.finish(
                task_id,
                TaskState::Succeeded,
                output,
                "",
                now_ms.saturating_add(1),
            ),
            Err(failure) if failure.outcome_unknown => {
                let actor = engine.machine_id.clone();
                engine.transition(
                    running,
                    TaskState::Unknown,
                    now_ms.saturating_add(1),
                    &actor,
                    &failure.detail,
                    true,
                    Vec::new(),
                    &failure.code,
                )
            }
            Err(failure) => engine.fail_attempt(
                task_id,
                &failure.code,
                failure.retryable,
                failure.retry_after_ms,
                now_ms.saturating_add(1),
            ),
        }
    }
}

#[derive(Debug)]
pub struct TaskEngine<J: Journal> {
    machine_id: String,
    journal: J,
    tasks: BTreeMap<String, TaskRecord>,
    idempotency: BTreeMap<(String, String), String>,
    leases: BTreeMap<String, Lease>,
    capabilities: BTreeMap<String, CapabilityPolicy>,
    next_sequence: u64,
}

impl<J: Journal> TaskEngine<J> {
    pub fn open(machine_id: impl Into<String>, journal: J) -> Result<Self, TaskError> {
        let machine_id = machine_id.into();
        let events = journal.load()?;
        let mut engine = Self {
            machine_id,
            journal,
            tasks: BTreeMap::new(),
            idempotency: BTreeMap::new(),
            leases: BTreeMap::new(),
            capabilities: BTreeMap::new(),
            next_sequence: 1,
        };
        for event in events {
            engine.next_sequence = engine.next_sequence.max(event.sequence + 1);
            match event.payload {
                JournalPayload::Task { snapshot } => {
                    engine.idempotency.insert(
                        (
                            snapshot.issuer_machine_id.clone(),
                            snapshot.idempotency_key.clone(),
                        ),
                        snapshot.task_id.clone(),
                    );
                    engine.tasks.insert(snapshot.task_id.clone(), snapshot);
                }
                JournalPayload::Lease { snapshot } => {
                    engine.leases.insert(snapshot.lease_id.clone(), snapshot);
                }
            }
        }
        Ok(engine)
    }

    pub fn tasks(&self) -> &BTreeMap<String, TaskRecord> {
        &self.tasks
    }

    pub fn leases(&self) -> &BTreeMap<String, Lease> {
        &self.leases
    }

    pub fn register_capability(
        &mut self,
        capability: impl Into<String>,
        interruptible: bool,
    ) -> Result<(), TaskError> {
        let capability = capability.into();
        if capability.is_empty() || capability.len() > MAX_IDENTIFIER_BYTES {
            return Err(TaskError::Invalid("capability policy is invalid"));
        }
        if self.capabilities.contains_key(&capability) {
            return Err(TaskError::Conflict(format!(
                "capability policy already registered for {capability}"
            )));
        }
        self.capabilities.insert(
            capability.clone(),
            CapabilityPolicy {
                capability,
                interruptible,
            },
        );
        Ok(())
    }

    pub fn journal(&self) -> &J {
        &self.journal
    }

    pub fn latest_sequence(&self) -> u64 {
        self.next_sequence.saturating_sub(1)
    }

    pub fn into_journal(self) -> J {
        self.journal
    }

    pub fn grant_lease(&mut self, lease: Lease, now_ms: u64) -> Result<(), TaskError> {
        validate_lease(&lease, &self.machine_id, now_ms)?;
        if self.leases.contains_key(&lease.lease_id) {
            return Err(TaskError::Conflict(
                "lease identifier already exists".into(),
            ));
        }
        if lease.exclusive {
            for current in self.leases.values() {
                if current.status == LeaseStatus::Active
                    && current.expires_at_ms > now_ms
                    && current.exclusive
                    && overlaps(&current.allowed_capabilities, &lease.allowed_capabilities)
                {
                    return Err(TaskError::Conflict(format!(
                        "exclusive authority overlaps lease {}",
                        current.lease_id
                    )));
                }
            }
        }
        self.append_lease(lease, now_ms, "lease granted", "granted")
    }

    pub fn renew_lease(
        &mut self,
        lease_id: &str,
        holder: &str,
        previous_revision: u64,
        expires_at_ms: u64,
        now_ms: u64,
    ) -> Result<(), TaskError> {
        let current = self
            .leases
            .get(lease_id)
            .cloned()
            .ok_or_else(|| TaskError::UnknownLease(lease_id.into()))?;
        if current.holder_machine_id != holder || !current.renewable {
            return Err(TaskError::AuthorityDenied(
                "lease cannot be renewed by holder".into(),
            ));
        }
        if current.status != LeaseStatus::Active || lease_valid_until(&current) <= now_ms {
            return Err(TaskError::AuthorityDenied(
                "lease is no longer active".into(),
            ));
        }
        if current.revision != previous_revision || expires_at_ms <= current.expires_at_ms {
            return Err(TaskError::Conflict(
                "invalid lease renewal revision or expiry".into(),
            ));
        }
        let mut renewed = current;
        renewed.revision += 1;
        renewed.expires_at_ms = expires_at_ms;
        self.append_lease(renewed, now_ms, "lease renewed", "active")
    }

    pub fn revoke_lease(
        &mut self,
        lease_id: &str,
        actor: &str,
        previous_revision: u64,
        now_ms: u64,
    ) -> Result<Vec<String>, TaskError> {
        let current = self
            .leases
            .get(lease_id)
            .cloned()
            .ok_or_else(|| TaskError::UnknownLease(lease_id.into()))?;
        if actor != current.grantor_machine_id || current.revision != previous_revision {
            return Err(TaskError::AuthorityDenied(
                "only grantor may revoke current revision".into(),
            ));
        }
        let mut revoked = current;
        revoked.revision += 1;
        revoked.status = LeaseStatus::Revoked;
        self.append_lease(revoked, now_ms, "lease revoked", "revoked")?;
        self.invalidate_tasks_for_lease(lease_id, now_ms, "authority lease revoked")
    }

    pub fn expire_leases(&mut self, now_ms: u64) -> Result<Vec<String>, TaskError> {
        let ids: Vec<_> = self
            .leases
            .values()
            .filter(|lease| {
                lease.status == LeaseStatus::Active && lease_valid_until(lease) <= now_ms
            })
            .map(|lease| lease.lease_id.clone())
            .collect();
        let mut affected = Vec::new();
        for lease_id in ids {
            let mut expired = self.leases[&lease_id].clone();
            expired.revision += 1;
            expired.status = LeaseStatus::Expired;
            self.append_lease(expired, now_ms, "lease expired", "expired")?;
            affected.extend(self.invalidate_tasks_for_lease(
                &lease_id,
                now_ms,
                "authority lease expired",
            )?);
        }
        Ok(affected)
    }

    pub fn submit(&mut self, request: NewTask, now_ms: u64) -> Result<TaskRecord, TaskError> {
        validate_task(&request, now_ms)?;
        let digest = hex_digest(&request.input);
        if let Some(existing_id) = self.idempotency.get(&(
            request.issuer_machine_id.clone(),
            request.idempotency_key.clone(),
        )) {
            let existing = &self.tasks[existing_id];
            if existing.task_id == request.task_id
                && existing.capability == request.capability
                && existing.input_sha256 == digest
                && existing.input_content_type == request.input_content_type
                && existing.authority_lease_id == request.authority_lease_id
                && existing.retry_policy == request.retry_policy
            {
                return Ok(existing.clone());
            }
            return Err(TaskError::Conflict(
                "idempotency key reused for different work".into(),
            ));
        }
        if let Some(existing) = self.tasks.get(&request.task_id) {
            return if existing.issuer_machine_id == request.issuer_machine_id
                && existing.capability == request.capability
                && existing.input_sha256 == digest
                && existing.input_content_type == request.input_content_type
            {
                Ok(existing.clone())
            } else {
                Err(TaskError::Conflict(
                    "task identifier reused for different work".into(),
                ))
            };
        }
        if self.tasks.len() >= MAX_TASKS {
            return Err(TaskError::Invalid("task capacity reached"));
        }
        self.authorize(
            &request.authority_lease_id,
            &request.issuer_machine_id,
            &request.capability,
            now_ms,
        )?;
        let capability_policy = self.capabilities.get(&request.capability).ok_or_else(|| {
            TaskError::AuthorityDenied(format!(
                "capability {} is not registered by executor",
                request.capability
            ))
        })?;
        let actor = request.issuer_machine_id.clone();
        let causation_id = request.causation_id.clone();
        let record = TaskRecord {
            task_id: request.task_id,
            issuer_machine_id: request.issuer_machine_id,
            executor_machine_id: self.machine_id.clone(),
            capability: request.capability,
            input_sha256: digest,
            input: request.input,
            input_content_type: request.input_content_type,
            authority_lease_id: request.authority_lease_id,
            idempotency_key: request.idempotency_key,
            retry_policy: request.retry_policy,
            deadline_ms: request.deadline_ms,
            maximum_attempts: request.maximum_attempts,
            attempts_started: 0,
            next_attempt_at_ms: 0,
            interruptible: capability_policy.interruptible,
            state: TaskState::Accepted,
            revision: 1,
            inspection_required: false,
            result: Vec::new(),
            error_code: String::new(),
            correlation_id: request.correlation_id.clone(),
        };
        self.append_task(
            record.clone(),
            now_ms,
            &actor,
            &causation_id,
            None,
            "task accepted",
        )?;
        Ok(record)
    }

    pub fn start(&mut self, task_id: &str, now_ms: u64) -> Result<ExecutionPermit, TaskError> {
        let current = self.task(task_id)?.clone();
        if !matches!(current.state, TaskState::Accepted | TaskState::RetryPending) {
            return Err(invalid_transition(&current, TaskState::Running));
        }
        if now_ms >= current.deadline_ms
            || current.attempts_started >= current.maximum_attempts
            || (current.state == TaskState::RetryPending && now_ms < current.next_attempt_at_ms)
        {
            return Err(TaskError::AuthorityDenied(
                "task deadline or attempt bound reached".into(),
            ));
        }
        self.authorize(
            &current.authority_lease_id,
            &current.issuer_machine_id,
            &current.capability,
            now_ms,
        )?;
        let mut running = current.clone();
        running.state = TaskState::Running;
        running.revision += 1;
        running.attempts_started += 1;
        running.next_attempt_at_ms = 0;
        self.append_task(
            running.clone(),
            now_ms,
            &self.machine_id.clone(),
            "",
            Some(current.state),
            "execution permitted",
        )?;
        Ok(ExecutionPermit {
            task_id: task_id.into(),
            capability: running.capability,
            attempt: running.attempts_started,
        })
    }

    pub fn cancel(
        &mut self,
        task_id: &str,
        actor: &str,
        now_ms: u64,
    ) -> Result<TaskRecord, TaskError> {
        let current = self.task(task_id)?.clone();
        if actor != current.issuer_machine_id && actor != self.machine_id {
            return Err(TaskError::AuthorityDenied(
                "cancellation actor is not issuer or executor".into(),
            ));
        }
        let next = match current.state {
            TaskState::Accepted | TaskState::Requested | TaskState::RetryPending => {
                TaskState::Cancelled
            }
            TaskState::Running if current.interruptible => TaskState::CancelPending,
            TaskState::Running => return Ok(current),
            _ => return Err(invalid_transition(&current, TaskState::Cancelled)),
        };
        self.transition(
            current,
            next,
            now_ms,
            actor,
            "cancellation requested",
            false,
            Vec::new(),
            "",
        )
    }

    pub fn progress(
        &mut self,
        task_id: &str,
        progress_per_mille: u16,
        stage: &str,
        now_ms: u64,
    ) -> Result<TaskRecord, TaskError> {
        if progress_per_mille > 1_000 || stage.len() > MAX_IDENTIFIER_BYTES {
            return Err(TaskError::Invalid("progress value or stage is invalid"));
        }
        let mut record = self.task(task_id)?.clone();
        if !matches!(record.state, TaskState::Running | TaskState::CancelPending) {
            return Err(invalid_transition(&record, TaskState::Running));
        }
        let previous = record.state;
        record.revision += 1;
        self.append_task(
            record.clone(),
            now_ms,
            &self.machine_id.clone(),
            "",
            Some(previous),
            &format!("progress {progress_per_mille}/1000: {stage}"),
        )?;
        Ok(record)
    }

    pub fn finish(
        &mut self,
        task_id: &str,
        outcome: TaskState,
        result: Vec<u8>,
        error_code: &str,
        now_ms: u64,
    ) -> Result<TaskRecord, TaskError> {
        if !matches!(
            outcome,
            TaskState::Succeeded | TaskState::Failed | TaskState::Cancelled
        ) {
            return Err(TaskError::Invalid("finish outcome must be terminal"));
        }
        let current = self.task(task_id)?.clone();
        if !matches!(
            current.state,
            TaskState::Running | TaskState::CancelPending | TaskState::Unknown
        ) {
            return Err(invalid_transition(&current, outcome));
        }
        self.transition(
            current,
            outcome,
            now_ms,
            &self.machine_id.clone(),
            "handler outcome",
            false,
            result,
            error_code,
        )
    }

    pub fn fail_attempt(
        &mut self,
        task_id: &str,
        error_code: &str,
        retryable: bool,
        retry_after_ms: u64,
        now_ms: u64,
    ) -> Result<TaskRecord, TaskError> {
        let current = self.task(task_id)?.clone();
        if current.state != TaskState::Running {
            return Err(invalid_transition(&current, TaskState::Failed));
        }
        let can_retry = retryable
            && current.retry_policy == RetryPolicy::SafeToRetry
            && current.attempts_started < current.maximum_attempts
            && retry_after_ms > 0
            && now_ms.saturating_add(retry_after_ms) < current.deadline_ms;
        if can_retry {
            let previous = current.state;
            let mut pending = current;
            pending.state = TaskState::RetryPending;
            pending.revision += 1;
            pending.next_attempt_at_ms = now_ms.saturating_add(retry_after_ms);
            pending.error_code = error_code.into();
            self.append_task(
                pending.clone(),
                now_ms,
                &self.machine_id.clone(),
                "",
                Some(previous),
                "retry scheduled after bounded backoff",
            )?;
            Ok(pending)
        } else {
            self.transition(
                current,
                TaskState::Failed,
                now_ms,
                &self.machine_id.clone(),
                "execution attempt failed",
                false,
                Vec::new(),
                error_code,
            )
        }
    }

    pub fn recover_incomplete(&mut self, now_ms: u64) -> Result<Vec<String>, TaskError> {
        let ids: Vec<_> = self
            .tasks
            .values()
            .filter(|task| matches!(task.state, TaskState::Running | TaskState::CancelPending))
            .map(|task| task.task_id.clone())
            .collect();
        for task_id in &ids {
            let current = self.tasks[task_id].clone();
            self.transition(
                current,
                TaskState::Unknown,
                now_ms,
                &self.machine_id.clone(),
                "restart with uncertain execution outcome",
                true,
                Vec::new(),
                "ump.task.outcome_unknown",
            )?;
        }
        Ok(ids)
    }

    pub fn reconcile(
        &mut self,
        remote: &TaskRecord,
        now_ms: u64,
        peer_machine_id: &str,
    ) -> Result<TaskRecord, TaskError> {
        let local = self.task(&remote.task_id)?.clone();
        if local.issuer_machine_id != remote.issuer_machine_id
            || local.executor_machine_id != remote.executor_machine_id
            || local.capability != remote.capability
            || local.input_sha256 != remote.input_sha256
            || local.idempotency_key != remote.idempotency_key
        {
            return Err(TaskError::Conflict(
                "reconciliation identity fields differ".into(),
            ));
        }
        if local.state == remote.state || remote.revision <= local.revision {
            return Ok(local);
        }
        if can_reconcile_forward(local.state, remote.state) {
            let mut accepted = remote.clone();
            accepted.revision = remote.revision;
            self.append_task(
                accepted.clone(),
                now_ms,
                peer_machine_id,
                "",
                Some(local.state),
                "reconciled from authenticated peer",
            )?;
            return Ok(accepted);
        }
        if local.state.is_terminal() && remote.state.is_terminal() && local.state != remote.state {
            let mut unknown = local.clone();
            unknown.state = TaskState::Unknown;
            unknown.revision += 1;
            unknown.inspection_required = true;
            unknown.error_code = "ump.task.reconcile_conflict".into();
            self.append_task(
                unknown.clone(),
                now_ms,
                peer_machine_id,
                "",
                Some(local.state),
                "conflicting terminal reconciliation",
            )?;
            return Ok(unknown);
        }
        Err(TaskError::Conflict(
            "remote state does not extend local lifecycle".into(),
        ))
    }

    pub fn trace(&self, task_id: &str) -> Result<Vec<AuditEvent>, TaskError> {
        if !self.tasks.contains_key(task_id) {
            return Err(TaskError::UnknownTask(task_id.into()));
        }
        Ok(self
            .journal
            .load()?
            .into_iter()
            .filter(|event| matches!(&event.payload, JournalPayload::Task { snapshot } if snapshot.task_id == task_id))
            .collect())
    }

    fn authorize(
        &self,
        lease_id: &str,
        issuer: &str,
        capability: &str,
        now_ms: u64,
    ) -> Result<(), TaskError> {
        let lease = self
            .leases
            .get(lease_id)
            .ok_or_else(|| TaskError::UnknownLease(lease_id.into()))?;
        if lease.status != LeaseStatus::Active
            || lease.grantor_machine_id != self.machine_id
            || lease.holder_machine_id != issuer
            || !lease
                .allowed_capabilities
                .iter()
                .any(|allowed| allowed == capability)
            || lease_valid_until(lease) <= now_ms
        {
            return Err(TaskError::AuthorityDenied(format!(
                "lease {lease_id} does not authorize this action"
            )));
        }
        Ok(())
    }

    fn invalidate_tasks_for_lease(
        &mut self,
        lease_id: &str,
        now_ms: u64,
        reason: &str,
    ) -> Result<Vec<String>, TaskError> {
        let ids: Vec<_> = self
            .tasks
            .values()
            .filter(|task| {
                task.authority_lease_id == lease_id
                    && matches!(
                        task.state,
                        TaskState::Accepted
                            | TaskState::Running
                            | TaskState::CancelPending
                            | TaskState::RetryPending
                    )
            })
            .map(|task| task.task_id.clone())
            .collect();
        for task_id in &ids {
            let current = self.tasks[task_id].clone();
            let (next, inspect) = if current.state == TaskState::Accepted {
                (TaskState::Cancelled, false)
            } else {
                (TaskState::Unknown, true)
            };
            self.transition(
                current,
                next,
                now_ms,
                &self.machine_id.clone(),
                reason,
                inspect,
                Vec::new(),
                "ump.authority.lost",
            )?;
        }
        Ok(ids)
    }

    #[allow(clippy::too_many_arguments)]
    fn transition(
        &mut self,
        mut record: TaskRecord,
        next: TaskState,
        now_ms: u64,
        actor: &str,
        reason: &str,
        inspection_required: bool,
        result: Vec<u8>,
        error_code: &str,
    ) -> Result<TaskRecord, TaskError> {
        if record.state.is_terminal() || !allowed_transition(record.state, next) {
            return Err(invalid_transition(&record, next));
        }
        let previous = record.state;
        record.state = next;
        record.revision += 1;
        record.inspection_required = inspection_required;
        record.result = result;
        record.error_code = error_code.into();
        self.append_task(record.clone(), now_ms, actor, "", Some(previous), reason)?;
        Ok(record)
    }

    fn append_task(
        &mut self,
        record: TaskRecord,
        at_ms: u64,
        actor: &str,
        causation_id: &str,
        previous: Option<TaskState>,
        reason: &str,
    ) -> Result<(), TaskError> {
        let event = AuditEvent {
            sequence: self.next_sequence,
            at_ms,
            actor_machine_id: actor.into(),
            correlation_id: record.correlation_id.clone(),
            causation_id: causation_id.into(),
            previous_state: previous.map(|state| format!("{state:?}").to_lowercase()),
            next_state: format!("{:?}", record.state).to_lowercase(),
            reason: reason.into(),
            payload: JournalPayload::Task {
                snapshot: record.clone(),
            },
        };
        self.journal.append(&event)?;
        self.next_sequence += 1;
        self.idempotency.insert(
            (
                record.issuer_machine_id.clone(),
                record.idempotency_key.clone(),
            ),
            record.task_id.clone(),
        );
        self.tasks.insert(record.task_id.clone(), record);
        Ok(())
    }

    fn append_lease(
        &mut self,
        lease: Lease,
        at_ms: u64,
        reason: &str,
        next_state: &str,
    ) -> Result<(), TaskError> {
        let previous_state = self
            .leases
            .get(&lease.lease_id)
            .map(|value| format!("{:?}", value.status).to_lowercase());
        let event = AuditEvent {
            sequence: self.next_sequence,
            at_ms,
            actor_machine_id: lease.grantor_machine_id.clone(),
            correlation_id: lease.lease_id.clone(),
            causation_id: String::new(),
            previous_state,
            next_state: next_state.into(),
            reason: reason.into(),
            payload: JournalPayload::Lease {
                snapshot: lease.clone(),
            },
        };
        self.journal.append(&event)?;
        self.next_sequence += 1;
        self.leases.insert(lease.lease_id.clone(), lease);
        Ok(())
    }

    fn task(&self, task_id: &str) -> Result<&TaskRecord, TaskError> {
        self.tasks
            .get(task_id)
            .ok_or_else(|| TaskError::UnknownTask(task_id.into()))
    }
}

fn validate_lease(lease: &Lease, executor: &str, now_ms: u64) -> Result<(), TaskError> {
    if lease.lease_id.is_empty()
        || lease.holder_machine_id.is_empty()
        || lease.allowed_capabilities.is_empty()
    {
        return Err(TaskError::Invalid(
            "lease identity, holder, and capability scope are required",
        ));
    }
    if lease.grantor_machine_id != executor
        || lease.status != LeaseStatus::Active
        || lease.revision == 0
    {
        return Err(TaskError::Invalid(
            "lease grantor, state, or revision is invalid",
        ));
    }
    if lease.issued_at_ms > now_ms || lease_valid_until(lease) <= now_ms {
        return Err(TaskError::Invalid("lease validity interval is not active"));
    }
    Ok(())
}

fn validate_task(task: &NewTask, now_ms: u64) -> Result<(), TaskError> {
    if [
        &task.task_id,
        &task.issuer_machine_id,
        &task.capability,
        &task.authority_lease_id,
        &task.idempotency_key,
        &task.correlation_id,
    ]
    .iter()
    .any(|value| value.is_empty() || value.len() > MAX_IDENTIFIER_BYTES)
    {
        return Err(TaskError::Invalid(
            "required identifier missing or too long",
        ));
    }
    if task.input.len() > MAX_TASK_INPUT_BYTES
        || task.input_content_type.is_empty()
        || task.input_content_type.len() > MAX_IDENTIFIER_BYTES
        || task.maximum_attempts == 0
        || task.deadline_ms <= now_ms
    {
        return Err(TaskError::Invalid(
            "input, attempt bound, or deadline is invalid",
        ));
    }
    if task.retry_policy != RetryPolicy::SafeToRetry && task.maximum_attempts > 1 {
        return Err(TaskError::Invalid(
            "non-idempotent work cannot request multiple attempts",
        ));
    }
    Ok(())
}

fn lease_valid_until(lease: &Lease) -> u64 {
    lease
        .expires_at_ms
        .saturating_sub(lease.maximum_clock_uncertainty_ms)
}

fn overlaps(left: &[String], right: &[String]) -> bool {
    left.iter().any(|value| right.contains(value))
}

fn allowed_transition(from: TaskState, to: TaskState) -> bool {
    matches!(
        (from, to),
        (
            TaskState::Requested,
            TaskState::Accepted | TaskState::Rejected | TaskState::Cancelled
        ) | (
            TaskState::Accepted,
            TaskState::Running | TaskState::Cancelled | TaskState::Unknown
        ) | (
            TaskState::Running,
            TaskState::CancelPending
                | TaskState::RetryPending
                | TaskState::Succeeded
                | TaskState::Failed
                | TaskState::Cancelled
                | TaskState::Unknown
        ) | (
            TaskState::RetryPending,
            TaskState::Running | TaskState::Cancelled | TaskState::Unknown
        ) | (
            TaskState::CancelPending,
            TaskState::Cancelled | TaskState::Succeeded | TaskState::Failed | TaskState::Unknown
        ) | (
            TaskState::Unknown,
            TaskState::Succeeded | TaskState::Failed | TaskState::Cancelled
        )
    )
}

fn can_reconcile_forward(from: TaskState, to: TaskState) -> bool {
    allowed_transition(from, to)
        || matches!(
            (from, to),
            (
                TaskState::Requested,
                TaskState::Running
                    | TaskState::CancelPending
                    | TaskState::RetryPending
                    | TaskState::Succeeded
                    | TaskState::Failed
                    | TaskState::Unknown
            ) | (
                TaskState::Accepted,
                TaskState::CancelPending
                    | TaskState::RetryPending
                    | TaskState::Succeeded
                    | TaskState::Failed
            ) | (
                TaskState::RetryPending,
                TaskState::CancelPending | TaskState::Succeeded | TaskState::Failed
            )
        )
}

fn invalid_transition(record: &TaskRecord, to: TaskState) -> TaskError {
    TaskError::InvalidTransition {
        task_id: record.task_id.clone(),
        from: record.state,
        to,
    }
}

fn hex_digest(input: &[u8]) -> String {
    format!("{:x}", Sha256::digest(input))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };

    const EXECUTOR: &str = "ump:machine:delivery-1";
    const ISSUER: &str = "ump:machine:coordinator-1";
    const CAPABILITY: &str = "org.ump.logistics.deliver";

    fn lease(id: &str) -> Lease {
        Lease {
            lease_id: id.into(),
            grantor_machine_id: EXECUTOR.into(),
            holder_machine_id: ISSUER.into(),
            allowed_capabilities: vec![CAPABILITY.into()],
            resource_ids: vec!["ump:resource:delivery-zone".into()],
            issued_at_ms: 0,
            expires_at_ms: 10_000,
            maximum_clock_uncertainty_ms: 10,
            renewable: true,
            exclusive: true,
            status: LeaseStatus::Active,
            revision: 1,
        }
    }

    fn task(id: &str) -> NewTask {
        NewTask {
            task_id: id.into(),
            issuer_machine_id: ISSUER.into(),
            capability: CAPABILITY.into(),
            input: br#"{"package":"p1"}"#.to_vec(),
            input_content_type: "application/json".into(),
            authority_lease_id: "lease-1".into(),
            idempotency_key: format!("idem-{id}"),
            retry_policy: RetryPolicy::AtMostOnce,
            deadline_ms: 9_000,
            maximum_attempts: 1,
            correlation_id: format!("corr-{id}"),
            causation_id: format!("message-{id}"),
        }
    }

    fn engine() -> TaskEngine<MemoryJournal> {
        let mut engine = TaskEngine::open(EXECUTOR, MemoryJournal::default()).unwrap();
        engine.register_capability(CAPABILITY, true).unwrap();
        engine.grant_lease(lease("lease-1"), 1).unwrap();
        engine
    }

    #[test]
    fn duplicate_delivery_returns_record_without_duplicate_execution() {
        let mut engine = engine();
        let request = task("task-1");
        let first = engine.submit(request.clone(), 2).unwrap();
        let duplicate = engine.submit(request, 3).unwrap();
        assert_eq!(first, duplicate);
        assert_eq!(engine.start("task-1", 4).unwrap().attempt, 1);
        assert!(matches!(
            engine.start("task-1", 5),
            Err(TaskError::InvalidTransition { .. })
        ));
        assert_eq!(engine.tasks()["task-1"].attempts_started, 1);
    }

    #[test]
    fn rejects_conflicting_issuer_and_expired_command() {
        let mut engine = engine();
        let mut conflicting = task("task-2");
        conflicting.issuer_machine_id = "ump:machine:other".into();
        assert!(matches!(
            engine.submit(conflicting, 2),
            Err(TaskError::AuthorityDenied(_))
        ));

        let expired = task("task-3");
        assert_eq!(
            engine.submit(expired, 9_000).unwrap_err(),
            TaskError::Invalid("input, attempt bound, or deadline is invalid")
        );
    }

    #[test]
    fn cancellation_before_start_prevents_execution_and_during_run_is_visible() {
        let mut engine = engine();
        engine.submit(task("before"), 2).unwrap();
        assert_eq!(
            engine.cancel("before", ISSUER, 3).unwrap().state,
            TaskState::Cancelled
        );
        assert!(matches!(
            engine.start("before", 4),
            Err(TaskError::InvalidTransition { .. })
        ));

        engine.submit(task("during"), 5).unwrap();
        engine.start("during", 6).unwrap();
        assert_eq!(
            engine.cancel("during", ISSUER, 7).unwrap().state,
            TaskState::CancelPending
        );
        assert_eq!(
            engine
                .finish("during", TaskState::Cancelled, Vec::new(), "", 8)
                .unwrap()
                .state,
            TaskState::Cancelled
        );
    }

    #[test]
    fn lease_revocation_invalidates_running_work_without_claiming_an_outcome() {
        let mut engine = engine();
        engine.submit(task("task-4"), 2).unwrap();
        engine.start("task-4", 3).unwrap();
        let affected = engine.revoke_lease("lease-1", EXECUTOR, 1, 4).unwrap();
        assert_eq!(affected, vec!["task-4"]);
        let record = &engine.tasks()["task-4"];
        assert_eq!(record.state, TaskState::Unknown);
        assert!(record.inspection_required);
        assert!(matches!(
            engine.submit(task("task-5"), 5),
            Err(TaskError::AuthorityDenied(_))
        ));
    }

    #[test]
    fn durable_restart_recovers_running_task_as_unknown() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("task-journal.jsonl");
        {
            let mut engine = TaskEngine::open(EXECUTOR, FileJournal::new(&path)).unwrap();
            engine.register_capability(CAPABILITY, true).unwrap();
            engine.grant_lease(lease("lease-1"), 1).unwrap();
            engine.submit(task("task-6"), 2).unwrap();
            engine.start("task-6", 3).unwrap();
        }
        let mut restarted = TaskEngine::open(EXECUTOR, FileJournal::new(&path)).unwrap();
        assert_eq!(restarted.tasks()["task-6"].state, TaskState::Running);
        assert_eq!(restarted.recover_incomplete(4).unwrap(), vec!["task-6"]);
        assert_eq!(restarted.tasks()["task-6"].state, TaskState::Unknown);
        assert!(restarted.tasks()["task-6"].inspection_required);
        assert_eq!(restarted.trace("task-6").unwrap().len(), 3);
    }

    #[test]
    fn exclusive_leases_cannot_overlap() {
        let mut engine = engine();
        let mut second = lease("lease-2");
        second.holder_machine_id = "ump:machine:other".into();
        assert!(matches!(
            engine.grant_lease(second, 2),
            Err(TaskError::Conflict(_))
        ));
    }

    #[test]
    fn malformed_task_corpus_is_rejected_without_panic() {
        for size in [
            0,
            1,
            MAX_TASK_INPUT_BYTES,
            MAX_TASK_INPUT_BYTES + 1,
            100_000,
        ] {
            let mut engine = engine();
            let mut request = task(&format!("fuzz-{size}"));
            request.input = vec![0xa5; size];
            let result = engine.submit(request, 2);
            assert_eq!(result.is_ok(), size <= MAX_TASK_INPUT_BYTES);
        }
    }

    #[test]
    fn randomized_task_inputs_never_panic_or_bypass_bounds() {
        let mut random = 0x9e3779b97f4a7c15_u64;
        for index in 0..512 {
            random ^= random << 13;
            random ^= random >> 7;
            random ^= random << 17;
            let size = (random as usize) % (MAX_TASK_INPUT_BYTES + 4_096);
            let mut engine = engine();
            let mut request = task(&format!("random-{index}"));
            request.input = vec![(random >> 8) as u8; size];
            request.maximum_attempts = ((random >> 24) as u32) % 4;
            request.retry_policy = match (random >> 32) % 3 {
                0 => RetryPolicy::SafeToRetry,
                1 => RetryPolicy::AtMostOnce,
                _ => RetryPolicy::ReconcileRequired,
            };
            let accepted = engine.submit(request, 2).is_ok();
            if accepted {
                assert!(size <= MAX_TASK_INPUT_BYTES);
                let record = &engine.tasks()[&format!("random-{index}")];
                assert!((1..=3).contains(&record.maximum_attempts));
                assert!(
                    record.retry_policy == RetryPolicy::SafeToRetry || record.maximum_attempts == 1
                );
            }
        }
    }

    struct CountingHandler {
        calls: Arc<AtomicUsize>,
    }

    impl TaskHandler for CountingHandler {
        fn capability(&self) -> &str {
            CAPABILITY
        }

        fn interruptible(&self) -> bool {
            true
        }

        fn execute(&self, context: &TaskContext) -> Result<Vec<u8>, HandlerFailure> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            assert_eq!(context.input_content_type, "application/json");
            assert_eq!(context.attempt, 1);
            Ok(b"handler-result".to_vec())
        }
    }

    #[test]
    fn registered_adapter_executes_once_after_durable_permission() {
        let calls = Arc::new(AtomicUsize::new(0));
        let mut registry = HandlerRegistry::default();
        registry
            .register(CountingHandler {
                calls: Arc::clone(&calls),
            })
            .unwrap();
        let mut engine = engine();
        engine.submit(task("handler"), 2).unwrap();
        let result = registry.execute(&mut engine, "handler", 3).unwrap();
        assert_eq!(result.state, TaskState::Succeeded);
        assert_eq!(result.result, b"handler-result");
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        assert!(registry.execute(&mut engine, "handler", 4).is_err());
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn safe_retry_enforces_backoff_deadline_and_attempt_bound() {
        let mut engine = engine();
        let mut request = task("retry");
        request.retry_policy = RetryPolicy::SafeToRetry;
        request.maximum_attempts = 3;
        engine.submit(request, 2).unwrap();

        assert_eq!(engine.start("retry", 3).unwrap().attempt, 1);
        let pending = engine
            .fail_attempt("retry", "temporary", true, 100, 4)
            .unwrap();
        assert_eq!(pending.state, TaskState::RetryPending);
        assert_eq!(pending.next_attempt_at_ms, 104);
        assert!(matches!(
            engine.start("retry", 103),
            Err(TaskError::AuthorityDenied(_))
        ));

        assert_eq!(engine.start("retry", 104).unwrap().attempt, 2);
        engine
            .fail_attempt("retry", "temporary", true, 100, 105)
            .unwrap();
        assert_eq!(engine.start("retry", 205).unwrap().attempt, 3);
        let failed = engine
            .fail_attempt("retry", "temporary", true, 100, 206)
            .unwrap();
        assert_eq!(failed.state, TaskState::Failed);
        assert!(engine.start("retry", 306).is_err());
        assert_eq!(engine.tasks()["retry"].attempts_started, 3);
    }

    #[test]
    fn reconciliation_converges_forward_and_exposes_terminal_conflict() {
        let mut executor = engine();
        executor.submit(task("reconcile"), 2).unwrap();
        let accepted_journal = executor.journal().clone();
        executor.start("reconcile", 3).unwrap();
        let terminal = executor
            .finish("reconcile", TaskState::Succeeded, b"done".to_vec(), "", 4)
            .unwrap();

        let mut remote_view = TaskEngine::open(EXECUTOR, accepted_journal).unwrap();
        let converged = remote_view.reconcile(&terminal, 5, EXECUTOR).unwrap();
        assert_eq!(converged.state, TaskState::Succeeded);

        let mut conflicting = terminal;
        conflicting.state = TaskState::Failed;
        conflicting.revision = converged.revision + 1;
        let unknown = remote_view.reconcile(&conflicting, 6, EXECUTOR).unwrap();
        assert_eq!(unknown.state, TaskState::Unknown);
        assert!(unknown.inspection_required);
    }
}
