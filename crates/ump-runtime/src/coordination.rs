use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::task::MAX_IDENTIFIER_BYTES;

pub const MAX_RESERVATION_CLAIMS: usize = 64;
pub const MAX_HANDOFF_EVIDENCE_BYTES: usize = 64 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Concurrency {
    Exclusive,
    Shared,
    Capacity,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Resource {
    pub resource_id: String,
    pub resource_type: String,
    pub concurrency: Concurrency,
    pub capacity: u32,
    pub frame_id: String,
    pub revision: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Claim {
    pub resource_id: String,
    pub quantity: u32,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReservationState {
    Active,
    Released,
    Expired,
    Revoked,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Reservation {
    pub reservation_id: String,
    pub owner_machine_id: String,
    pub task_id: String,
    pub claims: Vec<Claim>,
    pub issued_at_ms: u64,
    pub expires_at_ms: u64,
    pub state: ReservationState,
    pub revision: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct SpatialContext {
    pub reference_frame_id: String,
    pub subject_frame_id: String,
    pub position_m: [f64; 3],
    pub orientation_xyzw: [f64; 4],
    pub source_time_ms: u64,
    pub maximum_age_ms: u64,
    pub position_uncertainty_m: f64,
    pub orientation_uncertainty_rad: f64,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HandoffState {
    Proposed,
    Prepared,
    Ready,
    Transferring,
    Committed,
    Aborted,
    Failed,
    Unknown,
}

impl HandoffState {
    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Committed | Self::Aborted | Self::Failed)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Evidence {
    pub actor_machine_id: String,
    pub evidence_type: String,
    pub evidence: Vec<u8>,
    pub observed_at_ms: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Handoff {
    pub handoff_id: String,
    pub task_id: String,
    pub source_machine_id: String,
    pub destination_machine_id: String,
    pub subject_id: String,
    pub reservation_id: String,
    pub transfer_context: SpatialContext,
    pub preconditions: Vec<String>,
    pub deadline_ms: u64,
    pub state: HandoffState,
    pub revision: u64,
    pub authoritative_owner_machine_id: String,
    pub evidence: Vec<Evidence>,
    pub retry_safe: bool,
    pub inspection_required: bool,
    pub failure_code: String,
    pub correlation_id: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum CoordinationPayload {
    Resource { snapshot: Resource },
    Reservation { snapshot: Reservation },
    Handoff { snapshot: Box<Handoff> },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CoordinationNotice {
    ReservationLost {
        reservation_id: String,
        task_id: String,
        affected_handoff_ids: Vec<String>,
    },
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CoordinationEvent {
    pub sequence: u64,
    pub at_ms: u64,
    pub actor_machine_id: String,
    pub correlation_id: String,
    pub causation_id: String,
    pub previous_state: Option<String>,
    pub next_state: String,
    pub reason: String,
    pub payload: CoordinationPayload,
}

pub trait CoordinationJournal {
    fn load(&self) -> Result<Vec<CoordinationEvent>, CoordinationError>;
    fn append(&mut self, event: &CoordinationEvent) -> Result<(), CoordinationError>;
}

#[derive(Clone, Debug, Default)]
pub struct MemoryCoordinationJournal {
    events: Vec<CoordinationEvent>,
}

impl MemoryCoordinationJournal {
    pub fn events(&self) -> &[CoordinationEvent] {
        &self.events
    }
}

impl CoordinationJournal for MemoryCoordinationJournal {
    fn load(&self) -> Result<Vec<CoordinationEvent>, CoordinationError> {
        Ok(self.events.clone())
    }

    fn append(&mut self, event: &CoordinationEvent) -> Result<(), CoordinationError> {
        self.events.push(event.clone());
        Ok(())
    }
}

#[derive(Debug)]
pub struct FileCoordinationJournal {
    path: PathBuf,
}

impl FileCoordinationJournal {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl CoordinationJournal for FileCoordinationJournal {
    fn load(&self) -> Result<Vec<CoordinationEvent>, CoordinationError> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let file = fs::File::open(&self.path).map_err(CoordinationError::journal)?;
        BufReader::new(file)
            .lines()
            .map(|line| {
                let line = line.map_err(CoordinationError::journal)?;
                serde_json::from_str(&line).map_err(CoordinationError::journal)
            })
            .collect()
    }

    fn append(&mut self, event: &CoordinationEvent) -> Result<(), CoordinationError> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(CoordinationError::journal)?;
        }
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(CoordinationError::journal)?;
        serde_json::to_writer(&mut file, event).map_err(CoordinationError::journal)?;
        file.write_all(b"\n").map_err(CoordinationError::journal)?;
        file.sync_data().map_err(CoordinationError::journal)
    }
}

#[derive(Debug, Error, PartialEq)]
pub enum CoordinationError {
    #[error("invalid coordination input: {0}")]
    Invalid(&'static str),
    #[error("resource {0} was not found")]
    UnknownResource(String),
    #[error("reservation {0} was not found")]
    UnknownReservation(String),
    #[error("handoff {0} was not found")]
    UnknownHandoff(String),
    #[error("resource conflict: {0}")]
    ResourceConflict(String),
    #[error("authorization denied: {0}")]
    Unauthorized(String),
    #[error("spatial context rejected: {0}")]
    Spatial(&'static str),
    #[error("invalid handoff transition {handoff_id}: {from:?} -> {to:?}")]
    InvalidTransition {
        handoff_id: String,
        from: HandoffState,
        to: HandoffState,
    },
    #[error("coordination conflict: {0}")]
    Conflict(String),
    #[error("journal failure: {0}")]
    Journal(String),
}

impl CoordinationError {
    fn journal(error: impl std::fmt::Display) -> Self {
        Self::Journal(error.to_string())
    }
}

#[derive(Debug)]
pub struct CoordinationEngine<J: CoordinationJournal> {
    authority_machine_id: String,
    journal: J,
    resources: BTreeMap<String, Resource>,
    reservations: BTreeMap<String, Reservation>,
    handoffs: BTreeMap<String, Handoff>,
    known_frames: BTreeSet<String>,
    notices: Vec<CoordinationNotice>,
    next_sequence: u64,
}

impl<J: CoordinationJournal> CoordinationEngine<J> {
    pub fn open(
        authority_machine_id: impl Into<String>,
        journal: J,
    ) -> Result<Self, CoordinationError> {
        let events = journal.load()?;
        let mut engine = Self {
            authority_machine_id: authority_machine_id.into(),
            journal,
            resources: BTreeMap::new(),
            reservations: BTreeMap::new(),
            handoffs: BTreeMap::new(),
            known_frames: BTreeSet::new(),
            notices: Vec::new(),
            next_sequence: 1,
        };
        for event in events {
            engine.next_sequence = engine.next_sequence.max(event.sequence + 1);
            match event.payload {
                CoordinationPayload::Resource { snapshot } => {
                    if !snapshot.frame_id.is_empty() {
                        engine.known_frames.insert(snapshot.frame_id.clone());
                    }
                    engine
                        .resources
                        .insert(snapshot.resource_id.clone(), snapshot);
                }
                CoordinationPayload::Reservation { snapshot } => {
                    engine
                        .reservations
                        .insert(snapshot.reservation_id.clone(), snapshot);
                }
                CoordinationPayload::Handoff { snapshot } => {
                    engine
                        .handoffs
                        .insert(snapshot.handoff_id.clone(), *snapshot);
                }
            }
        }
        Ok(engine)
    }

    pub fn resources(&self) -> &BTreeMap<String, Resource> {
        &self.resources
    }

    pub fn reservations(&self) -> &BTreeMap<String, Reservation> {
        &self.reservations
    }

    pub fn handoffs(&self) -> &BTreeMap<String, Handoff> {
        &self.handoffs
    }

    pub fn journal(&self) -> &J {
        &self.journal
    }

    pub fn into_journal(self) -> J {
        self.journal
    }

    pub fn latest_sequence(&self) -> u64 {
        self.next_sequence.saturating_sub(1)
    }

    pub fn drain_notices(&mut self) -> Vec<CoordinationNotice> {
        std::mem::take(&mut self.notices)
    }

    pub fn register_frame(&mut self, frame_id: impl Into<String>) -> Result<(), CoordinationError> {
        let frame_id = frame_id.into();
        validate_id(&frame_id)?;
        self.known_frames.insert(frame_id);
        Ok(())
    }

    pub fn register_resource(
        &mut self,
        resource: Resource,
        now_ms: u64,
    ) -> Result<(), CoordinationError> {
        validate_resource(&resource)?;
        if self.resources.contains_key(&resource.resource_id) {
            return Err(CoordinationError::Conflict(
                "resource identifier already registered".into(),
            ));
        }
        self.append_resource(resource, now_ms, "resource registered")
    }

    pub fn reserve(
        &mut self,
        reservation_id: impl Into<String>,
        owner_machine_id: impl Into<String>,
        task_id: impl Into<String>,
        mut claims: Vec<Claim>,
        expires_at_ms: u64,
        now_ms: u64,
    ) -> Result<Reservation, CoordinationError> {
        let reservation_id = reservation_id.into();
        let owner_machine_id = owner_machine_id.into();
        let task_id = task_id.into();
        validate_id(&reservation_id)?;
        validate_id(&owner_machine_id)?;
        validate_id(&task_id)?;
        if claims.is_empty() || claims.len() > MAX_RESERVATION_CLAIMS || expires_at_ms <= now_ms {
            return Err(CoordinationError::Invalid(
                "claims or reservation expiry is invalid",
            ));
        }
        if self.reservations.contains_key(&reservation_id) {
            return Err(CoordinationError::Conflict(
                "reservation identifier already exists".into(),
            ));
        }
        claims.sort_by(|left, right| left.resource_id.cmp(&right.resource_id));
        if claims
            .windows(2)
            .any(|pair| pair[0].resource_id == pair[1].resource_id)
        {
            return Err(CoordinationError::Invalid("duplicate resource claim"));
        }
        for claim in &claims {
            let resource = self
                .resources
                .get(&claim.resource_id)
                .ok_or_else(|| CoordinationError::UnknownResource(claim.resource_id.clone()))?;
            if claim.quantity == 0 {
                return Err(CoordinationError::Invalid("claim quantity is zero"));
            }
            let requested = match resource.concurrency {
                Concurrency::Exclusive => {
                    if claim.quantity != 1 {
                        return Err(CoordinationError::Invalid(
                            "exclusive claim quantity must be one",
                        ));
                    }
                    1
                }
                Concurrency::Shared => 0,
                Concurrency::Capacity => claim.quantity,
            };
            let used = self.active_quantity(&claim.resource_id, now_ms);
            if requested > 0 && used.saturating_add(requested) > resource.capacity {
                return Err(CoordinationError::ResourceConflict(
                    claim.resource_id.clone(),
                ));
            }
        }
        let reservation = Reservation {
            reservation_id,
            owner_machine_id,
            task_id,
            claims,
            issued_at_ms: now_ms,
            expires_at_ms,
            state: ReservationState::Active,
            revision: 1,
        };
        self.append_reservation(reservation.clone(), now_ms, None, "reservation granted")?;
        Ok(reservation)
    }

    pub fn release_reservation(
        &mut self,
        reservation_id: &str,
        actor: &str,
        previous_revision: u64,
        now_ms: u64,
    ) -> Result<Reservation, CoordinationError> {
        let current = self.reservation(reservation_id)?.clone();
        if actor != current.owner_machine_id && actor != self.authority_machine_id {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        if current.state != ReservationState::Active || current.revision != previous_revision {
            return Err(CoordinationError::Conflict(
                "reservation is inactive or revision is stale".into(),
            ));
        }
        let mut released = current.clone();
        released.state = ReservationState::Released;
        released.revision += 1;
        self.append_reservation(
            released.clone(),
            now_ms,
            Some(current.state),
            "reservation released",
        )?;
        self.handle_reservation_loss(reservation_id, now_ms, "reservation released")?;
        Ok(released)
    }

    pub fn revoke_reservation(
        &mut self,
        reservation_id: &str,
        actor: &str,
        previous_revision: u64,
        reason: &str,
        now_ms: u64,
    ) -> Result<Reservation, CoordinationError> {
        let current = self.reservation(reservation_id)?.clone();
        if actor != self.authority_machine_id {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        if current.state != ReservationState::Active || current.revision != previous_revision {
            return Err(CoordinationError::Conflict(
                "reservation is inactive or revision is stale".into(),
            ));
        }
        if reason.is_empty() || reason.len() > MAX_IDENTIFIER_BYTES {
            return Err(CoordinationError::Invalid(
                "reservation revocation reason is invalid",
            ));
        }
        let mut revoked = current.clone();
        revoked.state = ReservationState::Revoked;
        revoked.revision += 1;
        self.append_reservation(
            revoked.clone(),
            now_ms,
            Some(current.state),
            &format!("reservation revoked by authority: {reason}"),
        )?;
        self.handle_reservation_loss(reservation_id, now_ms, "reservation revoked")?;
        Ok(revoked)
    }

    pub fn expire_reservations(&mut self, now_ms: u64) -> Result<Vec<String>, CoordinationError> {
        let ids: Vec<_> = self
            .reservations
            .values()
            .filter(|reservation| {
                reservation.state == ReservationState::Active && now_ms >= reservation.expires_at_ms
            })
            .map(|reservation| reservation.reservation_id.clone())
            .collect();
        for reservation_id in &ids {
            let current = self.reservations[reservation_id].clone();
            let mut expired = current.clone();
            expired.state = ReservationState::Expired;
            expired.revision += 1;
            self.append_reservation(expired, now_ms, Some(current.state), "reservation expired")?;
            self.handle_reservation_loss(reservation_id, now_ms, "reservation expired")?;
        }
        Ok(ids)
    }

    pub fn propose_handoff(
        &mut self,
        mut handoff: Handoff,
        actor: &str,
        now_ms: u64,
        maximum_position_uncertainty_m: f64,
        maximum_orientation_uncertainty_rad: f64,
    ) -> Result<Handoff, CoordinationError> {
        validate_handoff_identity(&handoff)?;
        if actor != handoff.source_machine_id {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        if self.handoffs.contains_key(&handoff.handoff_id) {
            return Err(CoordinationError::Conflict(
                "handoff identifier already exists".into(),
            ));
        }
        if handoff.deadline_ms <= now_ms {
            return Err(CoordinationError::Invalid("handoff deadline expired"));
        }
        self.validate_spatial(
            &handoff.transfer_context,
            now_ms,
            maximum_position_uncertainty_m,
            maximum_orientation_uncertainty_rad,
        )?;
        let reservation = self.reservation(&handoff.reservation_id)?;
        if reservation.state != ReservationState::Active
            || reservation.expires_at_ms <= now_ms
            || reservation.owner_machine_id != handoff.source_machine_id
        {
            return Err(CoordinationError::ResourceConflict(
                "handoff reservation is not active for source".into(),
            ));
        }
        handoff.state = HandoffState::Proposed;
        handoff.revision = 1;
        handoff.authoritative_owner_machine_id = handoff.source_machine_id.clone();
        handoff.evidence.clear();
        handoff.retry_safe = true;
        handoff.inspection_required = false;
        handoff.failure_code.clear();
        self.append_handoff(handoff.clone(), now_ms, actor, None, "handoff proposed")?;
        Ok(handoff)
    }

    pub fn prepare_handoff(
        &mut self,
        handoff_id: &str,
        actor: &str,
        now_ms: u64,
    ) -> Result<Handoff, CoordinationError> {
        let current = self.handoff(handoff_id)?.clone();
        if actor != current.source_machine_id {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        self.validate_handoff_dependencies(&current, now_ms)?;
        self.transition_handoff(
            current,
            HandoffState::Prepared,
            now_ms,
            actor,
            "source prepared",
        )
    }

    pub fn mark_ready(
        &mut self,
        handoff_id: &str,
        actor: &str,
        now_ms: u64,
    ) -> Result<Handoff, CoordinationError> {
        let current = self.handoff(handoff_id)?.clone();
        if actor != current.destination_machine_id {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        self.validate_handoff_dependencies(&current, now_ms)?;
        self.transition_handoff(
            current,
            HandoffState::Ready,
            now_ms,
            actor,
            "destination ready",
        )
    }

    pub fn begin_transfer(
        &mut self,
        handoff_id: &str,
        actor: &str,
        now_ms: u64,
    ) -> Result<Handoff, CoordinationError> {
        let current = self.handoff(handoff_id)?.clone();
        if actor != current.source_machine_id {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        self.validate_handoff_dependencies(&current, now_ms)?;
        self.transition_handoff(
            current,
            HandoffState::Transferring,
            now_ms,
            actor,
            "physical transfer started",
        )
    }

    pub fn add_evidence(
        &mut self,
        handoff_id: &str,
        evidence: Evidence,
        now_ms: u64,
    ) -> Result<Handoff, CoordinationError> {
        let current = self.handoff(handoff_id)?.clone();
        if current.state != HandoffState::Transferring
            || (evidence.actor_machine_id != current.source_machine_id
                && evidence.actor_machine_id != current.destination_machine_id)
            || evidence.evidence_type.is_empty()
            || evidence.evidence.is_empty()
            || evidence.evidence.len() > MAX_HANDOFF_EVIDENCE_BYTES
            || evidence.observed_at_ms > now_ms
        {
            return Err(CoordinationError::Invalid("handoff evidence is invalid"));
        }
        if let Some(stored) = current
            .evidence
            .iter()
            .find(|stored| stored.actor_machine_id == evidence.actor_machine_id)
        {
            if stored.evidence_type == evidence.evidence_type
                && stored.evidence == evidence.evidence
            {
                return Ok(current);
            }
            let actor = evidence.actor_machine_id.clone();
            return self.mark_unknown(
                current,
                now_ms,
                &actor,
                "contradictory participant evidence",
            );
        }
        let actor = evidence.actor_machine_id.clone();
        let mut updated = current.clone();
        updated.evidence.push(evidence);
        updated.revision += 1;
        self.append_handoff(
            updated.clone(),
            now_ms,
            &actor,
            Some(current.state),
            "participant completion evidence recorded",
        )?;
        Ok(updated)
    }

    pub fn commit_handoff(
        &mut self,
        handoff_id: &str,
        actor: &str,
        now_ms: u64,
    ) -> Result<Handoff, CoordinationError> {
        let current = self.handoff(handoff_id)?.clone();
        if actor != self.authority_machine_id
            && actor != current.source_machine_id
            && actor != current.destination_machine_id
        {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        if current.state != HandoffState::Transferring
            || !has_evidence(&current, &current.source_machine_id)
            || !has_evidence(&current, &current.destination_machine_id)
        {
            return Err(CoordinationError::Conflict(
                "bilateral completion evidence is required".into(),
            ));
        }
        self.validate_handoff_dependencies(&current, now_ms)?;
        let mut committed = current.clone();
        committed.state = HandoffState::Committed;
        committed.revision += 1;
        committed.authoritative_owner_machine_id = committed.destination_machine_id.clone();
        committed.retry_safe = false;
        self.append_handoff(
            committed.clone(),
            now_ms,
            actor,
            Some(current.state),
            "bilateral evidence committed ownership",
        )?;
        Ok(committed)
    }

    pub fn abort_handoff(
        &mut self,
        handoff_id: &str,
        actor: &str,
        now_ms: u64,
        reason: &str,
    ) -> Result<Handoff, CoordinationError> {
        let current = self.handoff(handoff_id)?.clone();
        if actor != current.source_machine_id
            && actor != current.destination_machine_id
            && actor != self.authority_machine_id
        {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        if current.state == HandoffState::Transferring || !current.evidence.is_empty() {
            self.mark_unknown(current, now_ms, actor, reason)
        } else {
            self.transition_handoff(current, HandoffState::Aborted, now_ms, actor, reason)
        }
    }

    pub fn fail_handoff(
        &mut self,
        handoff_id: &str,
        actor: &str,
        now_ms: u64,
        failure_code: &str,
    ) -> Result<Handoff, CoordinationError> {
        let current = self.handoff(handoff_id)?.clone();
        if actor != current.source_machine_id
            && actor != current.destination_machine_id
            && actor != self.authority_machine_id
        {
            return Err(CoordinationError::Unauthorized(actor.into()));
        }
        if current.state == HandoffState::Transferring || !current.evidence.is_empty() {
            self.mark_unknown(current, now_ms, actor, failure_code)
        } else {
            let mut failed = self.transition_handoff(
                current,
                HandoffState::Failed,
                now_ms,
                actor,
                "handoff failed before physical transfer",
            )?;
            failed.failure_code = failure_code.into();
            // Persist the failure code as a second monotonic revision.
            let previous = failed.state;
            failed.revision += 1;
            self.append_handoff(failed.clone(), now_ms, actor, Some(previous), failure_code)?;
            Ok(failed)
        }
    }

    pub fn recover_incomplete(&mut self, now_ms: u64) -> Result<Vec<String>, CoordinationError> {
        let ids: Vec<_> = self
            .handoffs
            .values()
            .filter(|handoff| handoff.state == HandoffState::Transferring)
            .map(|handoff| handoff.handoff_id.clone())
            .collect();
        for handoff_id in &ids {
            let current = self.handoffs[handoff_id].clone();
            self.mark_unknown(
                current,
                now_ms,
                &self.authority_machine_id.clone(),
                "restart during physical transfer",
            )?;
        }
        Ok(ids)
    }

    pub fn expire_handoffs(&mut self, now_ms: u64) -> Result<Vec<String>, CoordinationError> {
        let ids: Vec<_> = self
            .handoffs
            .values()
            .filter(|handoff| {
                !handoff.state.is_terminal()
                    && handoff.state != HandoffState::Unknown
                    && (now_ms >= handoff.deadline_ms
                        || now_ms.saturating_sub(handoff.transfer_context.source_time_ms)
                            >= handoff.transfer_context.maximum_age_ms)
            })
            .map(|handoff| handoff.handoff_id.clone())
            .collect();
        for handoff_id in &ids {
            let current = self.handoffs[handoff_id].clone();
            if current.state == HandoffState::Transferring || !current.evidence.is_empty() {
                self.mark_unknown(
                    current,
                    now_ms,
                    &self.authority_machine_id.clone(),
                    "handoff deadline or spatial validity expired during transfer",
                )?;
            } else {
                self.transition_handoff(
                    current,
                    HandoffState::Aborted,
                    now_ms,
                    &self.authority_machine_id.clone(),
                    "handoff deadline or spatial validity expired before transfer",
                )?;
            }
        }
        Ok(ids)
    }

    pub fn reconcile_handoff(
        &mut self,
        remote: &Handoff,
        actor: &str,
        now_ms: u64,
    ) -> Result<Handoff, CoordinationError> {
        validate_ownership(remote)?;
        let local = self.handoff(&remote.handoff_id)?.clone();
        if identity_tuple(&local) != identity_tuple(remote) {
            return Err(CoordinationError::Conflict(
                "handoff reconciliation identity differs".into(),
            ));
        }
        if remote.revision <= local.revision {
            return Ok(local);
        }
        if local.state == HandoffState::Committed && remote.state != HandoffState::Committed {
            return Err(CoordinationError::Conflict(
                "committed ownership cannot be rolled back by reconciliation".into(),
            ));
        }
        if handoff_reachable(local.state, remote.state) {
            self.append_handoff(
                remote.clone(),
                now_ms,
                actor,
                Some(local.state),
                "reconciled authenticated handoff record",
            )?;
            return Ok(remote.clone());
        }
        self.mark_unknown(local, now_ms, actor, "contradictory handoff reconciliation")
    }

    pub fn validate_spatial(
        &self,
        context: &SpatialContext,
        now_ms: u64,
        maximum_position_uncertainty_m: f64,
        maximum_orientation_uncertainty_rad: f64,
    ) -> Result<(), CoordinationError> {
        validate_id(&context.reference_frame_id)?;
        validate_id(&context.subject_frame_id)?;
        if !self.known_frames.contains(&context.reference_frame_id) {
            return Err(CoordinationError::Spatial("reference frame is unresolved"));
        }
        if context.source_time_ms > now_ms
            || context.maximum_age_ms == 0
            || now_ms.saturating_sub(context.source_time_ms) >= context.maximum_age_ms
        {
            return Err(CoordinationError::Spatial("spatial context is stale"));
        }
        if !maximum_position_uncertainty_m.is_finite()
            || !maximum_orientation_uncertainty_rad.is_finite()
            || !context.position_uncertainty_m.is_finite()
            || !context.orientation_uncertainty_rad.is_finite()
            || context.position_uncertainty_m < 0.0
            || context.orientation_uncertainty_rad < 0.0
            || context.position_uncertainty_m > maximum_position_uncertainty_m
            || context.orientation_uncertainty_rad > maximum_orientation_uncertainty_rad
        {
            return Err(CoordinationError::Spatial(
                "spatial uncertainty exceeds policy",
            ));
        }
        if context.position_m.iter().any(|value| !value.is_finite())
            || context
                .orientation_xyzw
                .iter()
                .any(|value| !value.is_finite())
        {
            return Err(CoordinationError::Spatial("pose contains non-finite value"));
        }
        let norm_squared: f64 = context
            .orientation_xyzw
            .iter()
            .map(|value| value * value)
            .sum();
        if (norm_squared - 1.0).abs() > 1e-6 {
            return Err(CoordinationError::Spatial("quaternion is not normalized"));
        }
        Ok(())
    }

    fn active_quantity(&self, resource_id: &str, now_ms: u64) -> u32 {
        self.reservations
            .values()
            .filter(|reservation| {
                reservation.state == ReservationState::Active && reservation.expires_at_ms > now_ms
            })
            .flat_map(|reservation| reservation.claims.iter())
            .filter(|claim| claim.resource_id == resource_id)
            .map(|claim| claim.quantity)
            .sum()
    }

    fn validate_handoff_dependencies(
        &self,
        handoff: &Handoff,
        now_ms: u64,
    ) -> Result<(), CoordinationError> {
        if now_ms >= handoff.deadline_ms {
            return Err(CoordinationError::Conflict(
                "handoff deadline expired".into(),
            ));
        }
        let reservation = self.reservation(&handoff.reservation_id)?;
        if reservation.state != ReservationState::Active || reservation.expires_at_ms <= now_ms {
            return Err(CoordinationError::ResourceConflict(
                "handoff reservation is inactive".into(),
            ));
        }
        self.validate_spatial(&handoff.transfer_context, now_ms, f64::MAX, f64::MAX)
    }

    fn handle_reservation_loss(
        &mut self,
        reservation_id: &str,
        now_ms: u64,
        reason: &str,
    ) -> Result<(), CoordinationError> {
        let ids: Vec<_> = self
            .handoffs
            .values()
            .filter(|handoff| {
                handoff.reservation_id == reservation_id
                    && !handoff.state.is_terminal()
                    && handoff.state != HandoffState::Unknown
            })
            .map(|handoff| handoff.handoff_id.clone())
            .collect();
        let affected_handoff_ids = ids.clone();
        for handoff_id in ids {
            let current = self.handoffs[&handoff_id].clone();
            if current.state == HandoffState::Transferring || !current.evidence.is_empty() {
                self.mark_unknown(current, now_ms, &self.authority_machine_id.clone(), reason)?;
            } else {
                self.transition_handoff(
                    current,
                    HandoffState::Aborted,
                    now_ms,
                    &self.authority_machine_id.clone(),
                    reason,
                )?;
            }
        }
        let task_id = self.reservation(reservation_id)?.task_id.clone();
        self.notices.push(CoordinationNotice::ReservationLost {
            reservation_id: reservation_id.into(),
            task_id,
            affected_handoff_ids,
        });
        Ok(())
    }

    fn transition_handoff(
        &mut self,
        mut handoff: Handoff,
        next: HandoffState,
        now_ms: u64,
        actor: &str,
        reason: &str,
    ) -> Result<Handoff, CoordinationError> {
        if handoff.state.is_terminal() || !handoff_transition(handoff.state, next) {
            return Err(CoordinationError::InvalidTransition {
                handoff_id: handoff.handoff_id,
                from: handoff.state,
                to: next,
            });
        }
        let previous = handoff.state;
        handoff.state = next;
        handoff.revision += 1;
        handoff.retry_safe = !matches!(next, HandoffState::Transferring | HandoffState::Committed);
        validate_ownership(&handoff)?;
        self.append_handoff(handoff.clone(), now_ms, actor, Some(previous), reason)?;
        Ok(handoff)
    }

    fn mark_unknown(
        &mut self,
        mut handoff: Handoff,
        now_ms: u64,
        actor: &str,
        reason: &str,
    ) -> Result<Handoff, CoordinationError> {
        if handoff.state == HandoffState::Committed {
            handoff.authoritative_owner_machine_id = handoff.destination_machine_id.clone();
        } else {
            handoff.authoritative_owner_machine_id = handoff.source_machine_id.clone();
        }
        let previous = handoff.state;
        handoff.state = HandoffState::Unknown;
        handoff.revision += 1;
        handoff.retry_safe = false;
        handoff.inspection_required = true;
        handoff.failure_code = "ump.handoff.outcome_unknown".into();
        self.append_handoff(handoff.clone(), now_ms, actor, Some(previous), reason)?;
        Ok(handoff)
    }

    fn append_resource(
        &mut self,
        resource: Resource,
        now_ms: u64,
        reason: &str,
    ) -> Result<(), CoordinationError> {
        let event = CoordinationEvent {
            sequence: self.next_sequence,
            at_ms: now_ms,
            actor_machine_id: self.authority_machine_id.clone(),
            correlation_id: resource.resource_id.clone(),
            causation_id: String::new(),
            previous_state: None,
            next_state: "registered".into(),
            reason: reason.into(),
            payload: CoordinationPayload::Resource {
                snapshot: resource.clone(),
            },
        };
        self.journal.append(&event)?;
        self.next_sequence += 1;
        if !resource.frame_id.is_empty() {
            self.known_frames.insert(resource.frame_id.clone());
        }
        self.resources
            .insert(resource.resource_id.clone(), resource);
        Ok(())
    }

    fn append_reservation(
        &mut self,
        reservation: Reservation,
        now_ms: u64,
        previous: Option<ReservationState>,
        reason: &str,
    ) -> Result<(), CoordinationError> {
        let event = CoordinationEvent {
            sequence: self.next_sequence,
            at_ms: now_ms,
            actor_machine_id: reservation.owner_machine_id.clone(),
            correlation_id: reservation.reservation_id.clone(),
            causation_id: reservation.task_id.clone(),
            previous_state: previous.map(|state| format!("{state:?}").to_lowercase()),
            next_state: format!("{:?}", reservation.state).to_lowercase(),
            reason: reason.into(),
            payload: CoordinationPayload::Reservation {
                snapshot: reservation.clone(),
            },
        };
        self.journal.append(&event)?;
        self.next_sequence += 1;
        self.reservations
            .insert(reservation.reservation_id.clone(), reservation);
        Ok(())
    }

    fn append_handoff(
        &mut self,
        handoff: Handoff,
        now_ms: u64,
        actor: &str,
        previous: Option<HandoffState>,
        reason: &str,
    ) -> Result<(), CoordinationError> {
        validate_ownership(&handoff)?;
        let event = CoordinationEvent {
            sequence: self.next_sequence,
            at_ms: now_ms,
            actor_machine_id: actor.into(),
            correlation_id: handoff.correlation_id.clone(),
            causation_id: handoff.task_id.clone(),
            previous_state: previous.map(|state| format!("{state:?}").to_lowercase()),
            next_state: format!("{:?}", handoff.state).to_lowercase(),
            reason: reason.into(),
            payload: CoordinationPayload::Handoff {
                snapshot: Box::new(handoff.clone()),
            },
        };
        self.journal.append(&event)?;
        self.next_sequence += 1;
        self.handoffs.insert(handoff.handoff_id.clone(), handoff);
        Ok(())
    }

    fn reservation(&self, reservation_id: &str) -> Result<&Reservation, CoordinationError> {
        self.reservations
            .get(reservation_id)
            .ok_or_else(|| CoordinationError::UnknownReservation(reservation_id.into()))
    }

    fn handoff(&self, handoff_id: &str) -> Result<&Handoff, CoordinationError> {
        self.handoffs
            .get(handoff_id)
            .ok_or_else(|| CoordinationError::UnknownHandoff(handoff_id.into()))
    }
}

fn validate_id(value: &str) -> Result<(), CoordinationError> {
    if value.is_empty() || value.len() > MAX_IDENTIFIER_BYTES {
        return Err(CoordinationError::Invalid(
            "identifier is missing or too long",
        ));
    }
    Ok(())
}

fn validate_resource(resource: &Resource) -> Result<(), CoordinationError> {
    validate_id(&resource.resource_id)?;
    validate_id(&resource.resource_type)?;
    if resource.revision == 0
        || (resource.concurrency == Concurrency::Exclusive && resource.capacity != 1)
        || (resource.concurrency == Concurrency::Capacity && resource.capacity == 0)
    {
        return Err(CoordinationError::Invalid(
            "resource revision or capacity is invalid",
        ));
    }
    Ok(())
}

fn validate_handoff_identity(handoff: &Handoff) -> Result<(), CoordinationError> {
    for value in [
        &handoff.handoff_id,
        &handoff.task_id,
        &handoff.source_machine_id,
        &handoff.destination_machine_id,
        &handoff.subject_id,
        &handoff.reservation_id,
        &handoff.correlation_id,
    ] {
        validate_id(value)?;
    }
    if handoff.source_machine_id == handoff.destination_machine_id {
        return Err(CoordinationError::Invalid(
            "handoff participants must differ",
        ));
    }
    Ok(())
}

fn validate_ownership(handoff: &Handoff) -> Result<(), CoordinationError> {
    let expected = if handoff.state == HandoffState::Committed {
        &handoff.destination_machine_id
    } else {
        &handoff.source_machine_id
    };
    if &handoff.authoritative_owner_machine_id != expected {
        return Err(CoordinationError::Conflict(
            "handoff violates single-owner invariant".into(),
        ));
    }
    Ok(())
}

fn handoff_transition(from: HandoffState, to: HandoffState) -> bool {
    matches!(
        (from, to),
        (
            HandoffState::Proposed,
            HandoffState::Prepared
                | HandoffState::Aborted
                | HandoffState::Failed
                | HandoffState::Unknown
        ) | (
            HandoffState::Prepared,
            HandoffState::Ready
                | HandoffState::Aborted
                | HandoffState::Failed
                | HandoffState::Unknown
        ) | (
            HandoffState::Ready,
            HandoffState::Transferring
                | HandoffState::Aborted
                | HandoffState::Failed
                | HandoffState::Unknown
        ) | (
            HandoffState::Transferring,
            HandoffState::Committed
                | HandoffState::Aborted
                | HandoffState::Failed
                | HandoffState::Unknown
        ) | (
            HandoffState::Unknown,
            HandoffState::Committed | HandoffState::Aborted | HandoffState::Failed
        )
    )
}

fn handoff_reachable(from: HandoffState, to: HandoffState) -> bool {
    handoff_transition(from, to)
        || matches!(
            (from, to),
            (
                HandoffState::Proposed,
                HandoffState::Ready | HandoffState::Transferring | HandoffState::Committed
            ) | (
                HandoffState::Prepared,
                HandoffState::Transferring | HandoffState::Committed
            ) | (HandoffState::Ready, HandoffState::Committed)
        )
}

fn has_evidence(handoff: &Handoff, actor: &str) -> bool {
    handoff
        .evidence
        .iter()
        .any(|evidence| evidence.actor_machine_id == actor)
}

fn identity_tuple(handoff: &Handoff) -> (&str, &str, &str, &str, &str, &str) {
    (
        &handoff.task_id,
        &handoff.source_machine_id,
        &handoff.destination_machine_id,
        &handoff.subject_id,
        &handoff.reservation_id,
        &handoff.correlation_id,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const AUTHORITY: &str = "ump:machine:coordinator";
    const SOURCE: &str = "ump:machine:mobile-base";
    const DESTINATION: &str = "ump:machine:robot-arm";
    const ZONE: &str = "ump:resource:transfer-zone";

    fn context(source_time_ms: u64) -> SpatialContext {
        SpatialContext {
            reference_frame_id: "ump:frame:transfer-zone".into(),
            subject_frame_id: "ump:frame:package-1".into(),
            position_m: [1.0, 2.0, 0.5],
            orientation_xyzw: [0.0, 0.0, 0.0, 1.0],
            source_time_ms,
            maximum_age_ms: 1_000,
            position_uncertainty_m: 0.005,
            orientation_uncertainty_rad: 0.01,
        }
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
                    frame_id: "ump:frame:transfer-zone".into(),
                    revision: 1,
                },
                0,
            )
            .unwrap();
        engine
    }

    fn reserve(engine: &mut CoordinationEngine<MemoryCoordinationJournal>, id: &str) {
        engine
            .reserve(
                id,
                SOURCE,
                "ump:task:handoff-1",
                vec![Claim {
                    resource_id: ZONE.into(),
                    quantity: 1,
                }],
                10_000,
                1,
            )
            .unwrap();
    }

    fn handoff(reservation_id: &str) -> Handoff {
        Handoff {
            handoff_id: format!("ump:handoff:{reservation_id}"),
            task_id: "ump:task:handoff-1".into(),
            source_machine_id: SOURCE.into(),
            destination_machine_id: DESTINATION.into(),
            subject_id: "ump:subject:package-1".into(),
            reservation_id: reservation_id.into(),
            transfer_context: context(1),
            preconditions: vec!["destination_gripper_empty".into()],
            deadline_ms: 9_000,
            state: HandoffState::Proposed,
            revision: 0,
            authoritative_owner_machine_id: String::new(),
            evidence: Vec::new(),
            retry_safe: true,
            inspection_required: false,
            failure_code: String::new(),
            correlation_id: "corr-handoff-1".into(),
        }
    }

    fn evidence(actor: &str, at_ms: u64) -> Evidence {
        Evidence {
            actor_machine_id: actor.into(),
            evidence_type: "subject_secured".into(),
            evidence: b"simulated-positive-evidence".to_vec(),
            observed_at_ms: at_ms,
        }
    }

    fn begin(engine: &mut CoordinationEngine<MemoryCoordinationJournal>, reservation_id: &str) {
        reserve(engine, reservation_id);
        let id = format!("ump:handoff:{reservation_id}");
        engine
            .propose_handoff(handoff(reservation_id), SOURCE, 2, 0.01, 0.02)
            .unwrap();
        engine.prepare_handoff(&id, SOURCE, 3).unwrap();
        engine.mark_ready(&id, DESTINATION, 4).unwrap();
        engine.begin_transfer(&id, SOURCE, 5).unwrap();
    }

    #[test]
    fn multi_resource_reservation_is_sorted_and_atomic() {
        let mut engine = engine();
        engine
            .register_resource(
                Resource {
                    resource_id: "ump:resource:tool".into(),
                    resource_type: "org.ump.resource.tool".into(),
                    concurrency: Concurrency::Capacity,
                    capacity: 2,
                    frame_id: String::new(),
                    revision: 1,
                },
                0,
            )
            .unwrap();
        let reservation = engine
            .reserve(
                "reservation-a",
                SOURCE,
                "task-a",
                vec![
                    Claim {
                        resource_id: ZONE.into(),
                        quantity: 1,
                    },
                    Claim {
                        resource_id: "ump:resource:tool".into(),
                        quantity: 2,
                    },
                ],
                100,
                1,
            )
            .unwrap();
        assert_eq!(reservation.claims[0].resource_id, "ump:resource:tool");

        let denied = engine.reserve(
            "reservation-b",
            DESTINATION,
            "task-b",
            vec![
                Claim {
                    resource_id: "ump:resource:tool".into(),
                    quantity: 1,
                },
                Claim {
                    resource_id: ZONE.into(),
                    quantity: 1,
                },
            ],
            100,
            2,
        );
        assert!(matches!(
            denied,
            Err(CoordinationError::ResourceConflict(_))
        ));
        assert!(!engine.reservations().contains_key("reservation-b"));
    }

    #[test]
    fn spatial_context_must_be_resolved_fresh_finite_and_normalized() {
        let engine = engine();
        let valid = context(10);
        engine.validate_spatial(&valid, 11, 0.01, 0.02).unwrap();

        let mut stale = valid.clone();
        stale.maximum_age_ms = 1;
        assert!(matches!(
            engine.validate_spatial(&stale, 11, 0.01, 0.02),
            Err(CoordinationError::Spatial("spatial context is stale"))
        ));
        let mut unresolved = valid.clone();
        unresolved.reference_frame_id = "ump:frame:missing".into();
        assert!(matches!(
            engine.validate_spatial(&unresolved, 11, 0.01, 0.02),
            Err(CoordinationError::Spatial("reference frame is unresolved"))
        ));
        let mut invalid_rotation = valid;
        invalid_rotation.orientation_xyzw = [0.0, 0.0, 0.0, 2.0];
        assert!(matches!(
            engine.validate_spatial(&invalid_rotation, 11, 0.01, 0.02),
            Err(CoordinationError::Spatial("quaternion is not normalized"))
        ));
    }

    #[test]
    fn bilateral_commit_changes_owner_exactly_once() {
        let mut engine = engine();
        begin(&mut engine, "reservation-happy");
        let handoff_id = "ump:handoff:reservation-happy";
        assert_eq!(
            engine.handoffs()[handoff_id].authoritative_owner_machine_id,
            SOURCE
        );
        engine
            .add_evidence(handoff_id, evidence(SOURCE, 6), 6)
            .unwrap();
        assert!(engine.commit_handoff(handoff_id, AUTHORITY, 7).is_err());
        engine
            .add_evidence(handoff_id, evidence(DESTINATION, 7), 7)
            .unwrap();
        let committed = engine.commit_handoff(handoff_id, AUTHORITY, 8).unwrap();
        assert_eq!(committed.state, HandoffState::Committed);
        assert_eq!(committed.authoritative_owner_machine_id, DESTINATION);
        assert!(engine.commit_handoff(handoff_id, AUTHORITY, 9).is_err());
    }

    #[test]
    fn reservation_loss_aborts_before_transfer_and_is_unknown_during_transfer() {
        let mut before = engine();
        reserve(&mut before, "reservation-before");
        before
            .propose_handoff(handoff("reservation-before"), SOURCE, 2, 0.01, 0.02)
            .unwrap();
        before.expire_reservations(10_000).unwrap();
        assert_eq!(
            before.handoffs()["ump:handoff:reservation-before"].state,
            HandoffState::Aborted
        );
        assert!(matches!(
            before.drain_notices().as_slice(),
            [CoordinationNotice::ReservationLost { task_id, .. }]
                if task_id == "ump:task:handoff-1"
        ));

        let mut during = engine();
        begin(&mut during, "reservation-during");
        during.expire_reservations(10_000).unwrap();
        let uncertain = &during.handoffs()["ump:handoff:reservation-during"];
        assert_eq!(uncertain.state, HandoffState::Unknown);
        assert_eq!(uncertain.authoritative_owner_machine_id, SOURCE);
        assert!(uncertain.inspection_required);
        assert!(!uncertain.retry_safe);
    }

    #[test]
    fn durable_restart_during_transfer_never_infers_commit() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("coordination.jsonl");
        {
            let mut engine =
                CoordinationEngine::open(AUTHORITY, FileCoordinationJournal::new(&path)).unwrap();
            engine
                .register_resource(
                    Resource {
                        resource_id: ZONE.into(),
                        resource_type: "org.ump.resource.transfer_zone".into(),
                        concurrency: Concurrency::Exclusive,
                        capacity: 1,
                        frame_id: "ump:frame:transfer-zone".into(),
                        revision: 1,
                    },
                    0,
                )
                .unwrap();
            engine
                .reserve(
                    "reservation-crash",
                    SOURCE,
                    "ump:task:handoff-1",
                    vec![Claim {
                        resource_id: ZONE.into(),
                        quantity: 1,
                    }],
                    10_000,
                    1,
                )
                .unwrap();
            let id = "ump:handoff:reservation-crash";
            engine
                .propose_handoff(handoff("reservation-crash"), SOURCE, 2, 0.01, 0.02)
                .unwrap();
            engine.prepare_handoff(id, SOURCE, 3).unwrap();
            engine.mark_ready(id, DESTINATION, 4).unwrap();
            engine.begin_transfer(id, SOURCE, 5).unwrap();
        }
        let mut restarted =
            CoordinationEngine::open(AUTHORITY, FileCoordinationJournal::new(&path)).unwrap();
        assert_eq!(
            restarted.recover_incomplete(6).unwrap(),
            vec!["ump:handoff:reservation-crash"]
        );
        let recovered = &restarted.handoffs()["ump:handoff:reservation-crash"];
        assert_eq!(recovered.state, HandoffState::Unknown);
        assert_eq!(recovered.authoritative_owner_machine_id, SOURCE);
        assert!(recovered.inspection_required);
    }

    #[test]
    fn reconciliation_preserves_committed_ownership() {
        let mut executor = engine();
        begin(&mut executor, "reservation-reconcile");
        let id = "ump:handoff:reservation-reconcile";
        let disconnected_journal = executor.journal().clone();
        executor.add_evidence(id, evidence(SOURCE, 6), 6).unwrap();
        executor
            .add_evidence(id, evidence(DESTINATION, 7), 7)
            .unwrap();
        let committed = executor.commit_handoff(id, AUTHORITY, 8).unwrap();

        let mut remote = CoordinationEngine::open(AUTHORITY, disconnected_journal).unwrap();
        let converged = remote.reconcile_handoff(&committed, AUTHORITY, 9).unwrap();
        assert_eq!(converged.authoritative_owner_machine_id, DESTINATION);
        let mut stale = committed;
        stale.state = HandoffState::Transferring;
        stale.authoritative_owner_machine_id = SOURCE.into();
        stale.revision += 1;
        assert!(remote.reconcile_handoff(&stale, SOURCE, 10).is_err());
        assert_eq!(
            remote.handoffs()[id].authoritative_owner_machine_id,
            DESTINATION
        );
    }

    #[test]
    fn bounded_restart_and_abort_points_preserve_single_owner() {
        for stage in 0..=4 {
            let reservation_id = format!("reservation-stage-{stage}");
            let handoff_id = format!("ump:handoff:{reservation_id}");
            let mut engine = engine();
            reserve(&mut engine, &reservation_id);
            engine
                .propose_handoff(handoff(&reservation_id), SOURCE, 2, 0.01, 0.02)
                .unwrap();
            if stage >= 1 {
                engine.prepare_handoff(&handoff_id, SOURCE, 3).unwrap();
            }
            if stage >= 2 {
                engine.mark_ready(&handoff_id, DESTINATION, 4).unwrap();
            }
            if stage >= 3 {
                engine.begin_transfer(&handoff_id, SOURCE, 5).unwrap();
            }
            if stage >= 4 {
                engine
                    .add_evidence(&handoff_id, evidence(SOURCE, 6), 6)
                    .unwrap();
                engine
                    .add_evidence(&handoff_id, evidence(DESTINATION, 7), 7)
                    .unwrap();
                engine.commit_handoff(&handoff_id, AUTHORITY, 8).unwrap();
            }

            let journal = engine.into_journal();
            let mut restarted = CoordinationEngine::open(AUTHORITY, journal).unwrap();
            restarted.recover_incomplete(9).unwrap();
            let recovered = restarted.handoffs()[&handoff_id].clone();
            let expected_owner = if stage == 4 { DESTINATION } else { SOURCE };
            assert_eq!(recovered.authoritative_owner_machine_id, expected_owner);
            assert_eq!(recovered.state == HandoffState::Committed, stage == 4);

            let abort_result =
                restarted.abort_handoff(&handoff_id, AUTHORITY, 10, "bounded abort point");
            if stage == 4 {
                assert!(abort_result.is_err());
                assert_eq!(
                    restarted.handoffs()[&handoff_id].authoritative_owner_machine_id,
                    DESTINATION
                );
            } else if recovered.state != HandoffState::Unknown {
                let aborted = abort_result.unwrap();
                assert_eq!(aborted.authoritative_owner_machine_id, SOURCE);
            }
        }
    }
}
