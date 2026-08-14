#![forbid(unsafe_code)]

pub mod coordination;
pub mod task;

use std::collections::{BTreeMap, BTreeSet, VecDeque};

use thiserror::Error;
use ump_protocol::v1::{
    Advertisement, Envelope, Heartbeat, Hello, MachineDescriptor, NegotiationRejected, StateUpdate,
    Welcome, envelope::Body,
};

pub const PROTOCOL_MAJOR: u32 = 1;
pub const PROTOCOL_MINOR: u32 = 2;
pub const DEFAULT_PRESENCE_TTL_MS: u64 = 2_000;
pub const MAX_SEEN_MESSAGES: usize = 4_096;
pub const MAX_TELEMETRY_SAMPLES: usize = 256;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthenticatedPeer {
    pub machine_id: String,
    pub can_read_metadata: bool,
    pub can_publish_metadata: bool,
    pub can_issue_tasks: bool,
    pub can_coordinate: bool,
}

impl AuthenticatedPeer {
    pub fn observer(machine_id: impl Into<String>) -> Self {
        Self {
            machine_id: machine_id.into(),
            can_read_metadata: true,
            can_publish_metadata: true,
            can_issue_tasks: true,
            can_coordinate: true,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PeerStatus {
    Present,
    Expired,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Peer {
    pub machine_id: String,
    pub session_id: String,
    pub selected_major: u32,
    pub selected_minor: u32,
    pub last_seen_ms: u64,
    pub expires_at_ms: u64,
    pub status: PeerStatus,
    pub last_heartbeat_sequence: u64,
    pub descriptor: Option<MachineDescriptor>,
    pub state: Option<StateUpdate>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ProtocolError {
    #[error("message is missing {0}")]
    MissingField(&'static str),
    #[error("message {message_id} expired at {expires_at_ms}")]
    Expired {
        message_id: String,
        expires_at_ms: u64,
    },
    #[error("malformed hello: {0}")]
    MalformedHello(&'static str),
    #[error("authenticated identity {authenticated} does not match envelope source {claimed}")]
    IdentityMismatch {
        authenticated: String,
        claimed: String,
    },
    #[error("peer {0} is not authorized to read protected machine metadata")]
    Unauthorized(String),
    #[error("duplicate message {0}")]
    DuplicateMessage(String),
    #[error("stale or replayed {kind} sequence {sequence} from {machine_id}")]
    Replay {
        kind: &'static str,
        sequence: u64,
        machine_id: String,
    },
    #[error("malformed machine metadata: {0}")]
    MalformedMetadata(&'static str),
    #[error("telemetry update contains {actual} samples; maximum is {maximum}")]
    TelemetryLimit { actual: usize, maximum: usize },
}

#[derive(Debug)]
pub struct Machine {
    machine_id: String,
    session_id: String,
    message_sequence: u64,
    heartbeat_sequence: u64,
    minimum_minor_version: u32,
    maximum_minor_version: u32,
    peers: BTreeMap<String, Peer>,
    local_descriptor: MachineDescriptor,
    local_state: StateUpdate,
    seen_messages: BTreeSet<(String, String)>,
    seen_order: VecDeque<(String, String)>,
}

impl Machine {
    pub fn new(machine_id: impl Into<String>, session_id: impl Into<String>) -> Self {
        Self {
            machine_id: machine_id.into(),
            session_id: session_id.into(),
            message_sequence: 0,
            heartbeat_sequence: 0,
            minimum_minor_version: 0,
            maximum_minor_version: PROTOCOL_MINOR,
            peers: BTreeMap::new(),
            local_descriptor: MachineDescriptor::default(),
            local_state: StateUpdate::default(),
            seen_messages: BTreeSet::new(),
            seen_order: VecDeque::new(),
        }
    }

    pub fn with_maximum_minor(mut self, maximum_minor_version: u32) -> Result<Self, ProtocolError> {
        if maximum_minor_version > PROTOCOL_MINOR
            || maximum_minor_version < self.minimum_minor_version
        {
            return Err(ProtocolError::MalformedHello(
                "configured minor exceeds implementation support",
            ));
        }
        self.maximum_minor_version = maximum_minor_version;
        Ok(self)
    }

    pub fn with_minimum_minor(mut self, minimum_minor_version: u32) -> Result<Self, ProtocolError> {
        if minimum_minor_version > self.maximum_minor_version {
            return Err(ProtocolError::MalformedHello(
                "configured minimum minor exceeds maximum support",
            ));
        }
        self.minimum_minor_version = minimum_minor_version;
        Ok(self)
    }

    pub fn with_descriptor(
        machine_id: impl Into<String>,
        session_id: impl Into<String>,
        descriptor: MachineDescriptor,
    ) -> Result<Self, ProtocolError> {
        validate_descriptor(&descriptor)?;
        let mut machine = Self::new(machine_id, session_id);
        machine.local_descriptor = descriptor;
        Ok(machine)
    }

    pub fn machine_id(&self) -> &str {
        &self.machine_id
    }

    pub fn peers(&self) -> &BTreeMap<String, Peer> {
        &self.peers
    }

    pub fn descriptor(&self) -> &MachineDescriptor {
        &self.local_descriptor
    }

    pub fn state(&self) -> &StateUpdate {
        &self.local_state
    }

    pub fn publish_state(&mut self, state: StateUpdate) -> Result<(), ProtocolError> {
        validate_state(&state)?;
        if state.revision <= self.local_state.revision && self.local_state.revision != 0 {
            return Err(ProtocolError::Replay {
                kind: "local state revision",
                sequence: state.revision,
                machine_id: self.machine_id.clone(),
            });
        }
        self.local_state = state;
        Ok(())
    }

    pub fn hello(&mut self, now_ms: u64) -> Envelope {
        let body = Body::Hello(Hello {
            supported_major_versions: vec![PROTOCOL_MAJOR],
            minimum_minor_version: self.minimum_minor_version,
            maximum_minor_version: self.maximum_minor_version,
            presence_ttl_ms: DEFAULT_PRESENCE_TTL_MS,
        });
        self.envelope(now_ms, Some(now_ms + DEFAULT_PRESENCE_TTL_MS), body)
    }

    pub fn heartbeat(&mut self, now_ms: u64) -> Envelope {
        self.heartbeat_sequence += 1;
        let body = Body::Heartbeat(Heartbeat {
            sequence: self.heartbeat_sequence,
            presence_ttl_ms: DEFAULT_PRESENCE_TTL_MS,
        });
        self.envelope(now_ms, Some(now_ms + DEFAULT_PRESENCE_TTL_MS), body)
    }

    pub fn advertisement(&mut self, now_ms: u64) -> Envelope {
        let body = Body::Advertisement(Advertisement {
            descriptor: Some(self.local_descriptor.clone()),
        });
        self.envelope(now_ms, Some(now_ms + DEFAULT_PRESENCE_TTL_MS), body)
    }

    pub fn state_update(&mut self, now_ms: u64) -> Envelope {
        let body = Body::StateUpdate(self.local_state.clone());
        self.envelope(now_ms, Some(now_ms + DEFAULT_PRESENCE_TTL_MS), body)
    }

    pub fn protocol_message(
        &mut self,
        now_ms: u64,
        correlation_id: impl Into<String>,
        causation_id: impl Into<String>,
        body: Body,
    ) -> Envelope {
        let mut envelope = self.envelope(now_ms, Some(now_ms + DEFAULT_PRESENCE_TTL_MS), body);
        envelope.correlation_id = correlation_id.into();
        envelope.causation_id = causation_id.into();
        envelope
    }

    pub fn receive(
        &mut self,
        envelope: Envelope,
        now_ms: u64,
    ) -> Result<Option<Envelope>, ProtocolError> {
        self.receive_inner(envelope, now_ms, None)
    }

    pub fn receive_authenticated(
        &mut self,
        envelope: Envelope,
        now_ms: u64,
        authenticated_peer: &AuthenticatedPeer,
    ) -> Result<Option<Envelope>, ProtocolError> {
        self.receive_inner(envelope, now_ms, Some(authenticated_peer))
    }

    fn receive_inner(
        &mut self,
        envelope: Envelope,
        now_ms: u64,
        authenticated_peer: Option<&AuthenticatedPeer>,
    ) -> Result<Option<Envelope>, ProtocolError> {
        validate_envelope(&envelope, now_ms)?;
        if let Some(peer) = authenticated_peer {
            if peer.machine_id != envelope.source_machine_id {
                return Err(ProtocolError::IdentityMismatch {
                    authenticated: peer.machine_id.clone(),
                    claimed: envelope.source_machine_id.clone(),
                });
            }
            if !peer.can_publish_metadata
                && matches!(
                    envelope.body,
                    Some(Body::Advertisement(_)) | Some(Body::StateUpdate(_))
                )
            {
                return Err(ProtocolError::Unauthorized(peer.machine_id.clone()));
            }
            if !peer.can_issue_tasks
                && matches!(
                    envelope.body,
                    Some(Body::TaskRequest(_))
                        | Some(Body::TaskCancel(_))
                        | Some(Body::TaskReconcileRequest(_))
                )
            {
                return Err(ProtocolError::Unauthorized(peer.machine_id.clone()));
            }
            if !peer.can_coordinate
                && matches!(
                    envelope.body,
                    Some(Body::ReservationRequest(_))
                        | Some(Body::ReservationRelease(_))
                        | Some(Body::HandoffProposal(_))
                        | Some(Body::HandoffUpdate(_))
                        | Some(Body::HandoffReconcileRequest(_))
                )
            {
                return Err(ProtocolError::Unauthorized(peer.machine_id.clone()));
            }
        }

        self.remember_message(&envelope)?;
        let source_machine_id = envelope.source_machine_id.clone();
        let source_session_id = envelope.source_session_id.clone();

        match envelope.body {
            Some(Body::Hello(hello)) => {
                validate_hello(&hello)?;
                let selected_major = hello
                    .supported_major_versions
                    .iter()
                    .copied()
                    .filter(|version| *version == PROTOCOL_MAJOR)
                    .max();

                let Some(selected_major) = selected_major else {
                    let rejection = Body::NegotiationRejected(NegotiationRejected {
                        code: "ump.negotiation.no_common_major".into(),
                        detail: format!(
                            "peer supports {:?}; local supports [{PROTOCOL_MAJOR}]",
                            hello.supported_major_versions
                        ),
                    });
                    return Ok(Some(self.envelope(
                        now_ms,
                        Some(now_ms + DEFAULT_PRESENCE_TTL_MS),
                        rejection,
                    )));
                };

                let selected_minor = self.maximum_minor_version.min(hello.maximum_minor_version);
                if hello.minimum_minor_version > selected_minor
                    || self.minimum_minor_version > selected_minor
                {
                    let rejection = Body::NegotiationRejected(NegotiationRejected {
                        code: "ump.negotiation.no_common_minor".into(),
                        detail: "no mutually supported minor version".into(),
                    });
                    return Ok(Some(self.envelope(
                        now_ms,
                        Some(now_ms + DEFAULT_PRESENCE_TTL_MS),
                        rejection,
                    )));
                }
                self.upsert_peer(
                    source_machine_id,
                    source_session_id,
                    selected_major,
                    selected_minor,
                    now_ms,
                    hello.presence_ttl_ms,
                );
                let welcome = Body::Welcome(Welcome {
                    selected_major_version: selected_major,
                    selected_minor_version: selected_minor,
                    presence_ttl_ms: DEFAULT_PRESENCE_TTL_MS,
                });
                Ok(Some(self.envelope(
                    now_ms,
                    Some(now_ms + DEFAULT_PRESENCE_TTL_MS),
                    welcome,
                )))
            }
            Some(Body::Heartbeat(heartbeat)) => {
                if let Some(peer) = self.peers.get_mut(&source_machine_id) {
                    if peer.session_id != source_session_id
                        || heartbeat.presence_ttl_ms == 0
                        || heartbeat.sequence <= peer.last_heartbeat_sequence
                    {
                        return Err(ProtocolError::Replay {
                            kind: "heartbeat",
                            sequence: heartbeat.sequence,
                            machine_id: source_machine_id,
                        });
                    }
                    peer.last_heartbeat_sequence = heartbeat.sequence;
                    peer.last_seen_ms = now_ms;
                    peer.expires_at_ms = now_ms + heartbeat.presence_ttl_ms;
                    peer.status = PeerStatus::Present;
                }
                Ok(None)
            }
            Some(Body::Welcome(welcome)) => {
                if welcome.selected_major_version != PROTOCOL_MAJOR
                    || welcome.selected_minor_version > self.maximum_minor_version
                    || welcome.selected_minor_version < self.minimum_minor_version
                {
                    return Err(ProtocolError::MalformedHello(
                        "welcome selected an unsupported version",
                    ));
                }
                self.upsert_peer(
                    source_machine_id,
                    source_session_id,
                    welcome.selected_major_version,
                    welcome.selected_minor_version,
                    now_ms,
                    welcome.presence_ttl_ms,
                );
                Ok(None)
            }
            Some(Body::Advertisement(advertisement)) => {
                let descriptor = advertisement
                    .descriptor
                    .ok_or(ProtocolError::MalformedMetadata("descriptor is missing"))?;
                validate_descriptor(&descriptor)?;
                let negotiated_minor = self
                    .peers
                    .get(&source_machine_id)
                    .map_or(0, |peer| peer.selected_minor);
                if negotiated_minor >= 2
                    && descriptor.deployment_mode
                        == ump_protocol::v1::DeploymentMode::Unspecified as i32
                {
                    return Err(ProtocolError::MalformedMetadata(
                        "deployment mode is required for protocol 1.2",
                    ));
                }
                if descriptor
                    .proxy
                    .as_ref()
                    .is_some_and(|proxy| proxy.represented_machine_id != source_machine_id)
                {
                    return Err(ProtocolError::MalformedMetadata(
                        "proxy represented machine does not match authenticated source",
                    ));
                }
                if let Some(peer) = self.peers.get_mut(&source_machine_id) {
                    let current_revision = peer
                        .descriptor
                        .as_ref()
                        .map_or(0, |current| current.revision);
                    if descriptor.revision <= current_revision {
                        return Err(ProtocolError::Replay {
                            kind: "capability revision",
                            sequence: descriptor.revision,
                            machine_id: source_machine_id,
                        });
                    }
                    peer.descriptor = Some(descriptor);
                }
                Ok(None)
            }
            Some(Body::StateUpdate(state)) => {
                validate_state(&state)?;
                if let Some(peer) = self.peers.get_mut(&source_machine_id) {
                    let current_revision =
                        peer.state.as_ref().map_or(0, |current| current.revision);
                    if state.revision <= current_revision {
                        return Err(ProtocolError::Replay {
                            kind: "state revision",
                            sequence: state.revision,
                            machine_id: source_machine_id,
                        });
                    }
                    peer.state = Some(state);
                }
                Ok(None)
            }
            Some(Body::NegotiationRejected(_))
            | Some(Body::ErrorReport(_))
            | Some(Body::LeaseGrant(_))
            | Some(Body::LeaseRenew(_))
            | Some(Body::LeaseRevoke(_))
            | Some(Body::TaskRequest(_))
            | Some(Body::TaskAcknowledgement(_))
            | Some(Body::TaskProgress(_))
            | Some(Body::TaskTerminal(_))
            | Some(Body::TaskCancel(_))
            | Some(Body::TaskReconcileRequest(_))
            | Some(Body::TaskReconcileResponse(_))
            | Some(Body::ReservationRequest(_))
            | Some(Body::ReservationResponse(_))
            | Some(Body::ReservationRelease(_))
            | Some(Body::HandoffProposal(_))
            | Some(Body::HandoffUpdate(_))
            | Some(Body::HandoffRecord(_))
            | Some(Body::HandoffReconcileRequest(_))
            | Some(Body::HandoffReconcileResponse(_))
            | None => Ok(None),
        }
    }

    pub fn expire_peers(&mut self, now_ms: u64) -> Vec<String> {
        let mut expired = Vec::new();
        for peer in self.peers.values_mut() {
            if peer.status == PeerStatus::Present && now_ms >= peer.expires_at_ms {
                peer.status = PeerStatus::Expired;
                expired.push(peer.machine_id.clone());
            }
        }
        expired
    }

    fn envelope(&mut self, now_ms: u64, expires_at_ms: Option<u64>, body: Body) -> Envelope {
        self.message_sequence += 1;
        Envelope {
            protocol_major: PROTOCOL_MAJOR,
            protocol_minor: self.maximum_minor_version,
            message_id: format!("{}-{}", self.session_id, self.message_sequence),
            source_machine_id: self.machine_id.clone(),
            source_session_id: self.session_id.clone(),
            sent_at_ms: now_ms,
            expires_at_ms,
            correlation_id: String::new(),
            causation_id: String::new(),
            body: Some(body),
        }
    }

    fn remember_message(&mut self, envelope: &Envelope) -> Result<(), ProtocolError> {
        let key = (
            envelope.source_session_id.clone(),
            envelope.message_id.clone(),
        );
        if !self.seen_messages.insert(key.clone()) {
            return Err(ProtocolError::DuplicateMessage(envelope.message_id.clone()));
        }
        self.seen_order.push_back(key);
        if self.seen_order.len() > MAX_SEEN_MESSAGES {
            if let Some(expired) = self.seen_order.pop_front() {
                self.seen_messages.remove(&expired);
            }
        }
        Ok(())
    }

    fn upsert_peer(
        &mut self,
        machine_id: String,
        session_id: String,
        selected_major: u32,
        selected_minor: u32,
        now_ms: u64,
        ttl_ms: u64,
    ) {
        self.peers.insert(
            machine_id.clone(),
            Peer {
                machine_id,
                session_id,
                selected_major,
                selected_minor,
                last_seen_ms: now_ms,
                expires_at_ms: now_ms + ttl_ms,
                status: PeerStatus::Present,
                last_heartbeat_sequence: 0,
                descriptor: None,
                state: None,
            },
        );
    }
}

fn validate_envelope(envelope: &Envelope, now_ms: u64) -> Result<(), ProtocolError> {
    if envelope.message_id.is_empty() {
        return Err(ProtocolError::MissingField("message_id"));
    }
    if envelope.source_machine_id.is_empty() {
        return Err(ProtocolError::MissingField("source_machine_id"));
    }
    if envelope.source_session_id.is_empty() {
        return Err(ProtocolError::MissingField("source_session_id"));
    }
    if matches!(
        envelope.body,
        Some(Body::TaskRequest(_))
            | Some(Body::TaskAcknowledgement(_))
            | Some(Body::TaskProgress(_))
            | Some(Body::TaskTerminal(_))
            | Some(Body::TaskCancel(_))
            | Some(Body::TaskReconcileRequest(_))
            | Some(Body::TaskReconcileResponse(_))
            | Some(Body::LeaseGrant(_))
            | Some(Body::LeaseRenew(_))
            | Some(Body::LeaseRevoke(_))
            | Some(Body::ReservationRequest(_))
            | Some(Body::ReservationResponse(_))
            | Some(Body::ReservationRelease(_))
            | Some(Body::HandoffProposal(_))
            | Some(Body::HandoffUpdate(_))
            | Some(Body::HandoffRecord(_))
            | Some(Body::HandoffReconcileRequest(_))
            | Some(Body::HandoffReconcileResponse(_))
    ) && envelope.correlation_id.is_empty()
    {
        return Err(ProtocolError::MissingField("correlation_id"));
    }
    if let Some(expires_at_ms) = envelope.expires_at_ms {
        if now_ms >= expires_at_ms {
            return Err(ProtocolError::Expired {
                message_id: envelope.message_id.clone(),
                expires_at_ms,
            });
        }
    }
    Ok(())
}

fn validate_hello(hello: &Hello) -> Result<(), ProtocolError> {
    if hello.supported_major_versions.is_empty() {
        return Err(ProtocolError::MalformedHello(
            "supported versions are empty",
        ));
    }
    if hello.minimum_minor_version > hello.maximum_minor_version {
        return Err(ProtocolError::MalformedHello(
            "minimum minor version exceeds maximum",
        ));
    }
    if hello.presence_ttl_ms == 0 {
        return Err(ProtocolError::MalformedHello("presence TTL is zero"));
    }
    Ok(())
}

fn validate_descriptor(descriptor: &MachineDescriptor) -> Result<(), ProtocolError> {
    if descriptor.machine_class.is_empty() {
        return Err(ProtocolError::MalformedMetadata("machine class is empty"));
    }
    if descriptor.revision == 0 {
        return Err(ProtocolError::MalformedMetadata(
            "descriptor revision is zero",
        ));
    }
    if descriptor
        .endpoints
        .iter()
        .any(|endpoint| endpoint.uri.is_empty())
    {
        return Err(ProtocolError::MalformedMetadata("endpoint URI is empty"));
    }
    if descriptor.capabilities.iter().any(|capability| {
        capability.r#type.is_empty() || capability.version.is_empty() || capability.revision == 0
    }) {
        return Err(ProtocolError::MalformedMetadata(
            "capability identity or revision is invalid",
        ));
    }
    let deployment = ump_protocol::v1::DeploymentMode::try_from(descriptor.deployment_mode)
        .unwrap_or(ump_protocol::v1::DeploymentMode::Unspecified);
    match (deployment, descriptor.proxy.as_ref()) {
        (ump_protocol::v1::DeploymentMode::Unspecified, None)
        | (ump_protocol::v1::DeploymentMode::Direct, None) => {}
        (ump_protocol::v1::DeploymentMode::GatewayProxy, Some(proxy))
            if proxy.gateway_id.starts_with("ump:gateway:")
                && proxy.represented_machine_id.starts_with("ump:machine:")
                && !proxy.controller_interface.trim().is_empty() => {}
        _ => {
            return Err(ProtocolError::MalformedMetadata(
                "deployment mode and proxy association are inconsistent",
            ));
        }
    }
    Ok(())
}

fn validate_state(state: &StateUpdate) -> Result<(), ProtocolError> {
    if state.revision == 0 {
        return Err(ProtocolError::MalformedMetadata("state revision is zero"));
    }
    if state.telemetry.len() > MAX_TELEMETRY_SAMPLES {
        return Err(ProtocolError::TelemetryLimit {
            actual: state.telemetry.len(),
            maximum: MAX_TELEMETRY_SAMPLES,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ump_protocol::v1::{
        Capability, CapabilityAvailability, Endpoint, OperationalState, ReservationRequest,
        SafetyState, TaskRequest, TransportKind,
    };

    fn machine() -> Machine {
        Machine::new("ump:machine:arm-1", "boot-arm-1")
    }

    fn descriptor(machine_class: &str, revision: u64) -> MachineDescriptor {
        MachineDescriptor {
            machine_class: machine_class.into(),
            manufacturer: "UMP Test".into(),
            model: "virtual".into(),
            software_version: "0.1.0".into(),
            endpoints: vec![Endpoint {
                uri: "quic://127.0.0.1:7443".into(),
                transport: TransportKind::Quic.into(),
                priority: 1,
            }],
            features: Vec::new(),
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
                revision: 1,
                constraints: Vec::new(),
            }],
            revision,
            deployment_mode: ump_protocol::v1::DeploymentMode::Direct.into(),
            proxy: None,
        }
    }

    #[test]
    fn gateway_proxy_descriptor_requires_complete_explicit_association() {
        let mut proxy = descriptor("robot_arm", 1);
        proxy.deployment_mode = ump_protocol::v1::DeploymentMode::GatewayProxy.into();
        proxy.proxy = Some(ump_protocol::v1::ProxyAssociation {
            gateway_id: "ump:gateway:cell-a".into(),
            represented_machine_id: "ump:machine:arm-1".into(),
            controller_interface: "vendor_https_v1".into(),
            read_only: false,
        });
        assert!(validate_descriptor(&proxy).is_ok());

        proxy.proxy.as_mut().unwrap().gateway_id = "ambiguous-host".into();
        assert!(matches!(
            validate_descriptor(&proxy),
            Err(ProtocolError::MalformedMetadata(_))
        ));
        proxy.proxy = None;
        assert!(matches!(
            validate_descriptor(&proxy),
            Err(ProtocolError::MalformedMetadata(_))
        ));
    }

    #[test]
    fn gateway_proxy_cannot_associate_a_different_authenticated_machine() {
        let mut proxy = descriptor("robot_arm", 1);
        proxy.deployment_mode = ump_protocol::v1::DeploymentMode::GatewayProxy.into();
        proxy.proxy = Some(ump_protocol::v1::ProxyAssociation {
            gateway_id: "ump:gateway:cell-a".into(),
            represented_machine_id: "ump:machine:different-arm".into(),
            controller_interface: "vendor_https_v1".into(),
            read_only: false,
        });
        let mut sender = Machine::with_descriptor("ump:machine:arm-1", "boot-arm", proxy).unwrap();
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base");
        let observer = AuthenticatedPeer::observer(sender.machine_id());
        receiver
            .receive_authenticated(sender.hello(0), 1, &observer)
            .unwrap();
        assert!(matches!(
            receiver.receive_authenticated(sender.advertisement(2), 2, &observer),
            Err(ProtocolError::MalformedMetadata(_))
        ));
    }

    #[test]
    fn rejects_expired_envelope() {
        let mut sender = machine();
        let message = sender.hello(0);
        let error = machine()
            .receive(message, DEFAULT_PRESENCE_TTL_MS)
            .unwrap_err();
        assert!(matches!(error, ProtocolError::Expired { .. }));
    }

    #[test]
    fn rejects_malformed_hello() {
        let mut sender = machine();
        let mut message = sender.hello(0);
        let Some(Body::Hello(hello)) = message.body.as_mut() else {
            panic!("expected hello")
        };
        hello.supported_major_versions.clear();
        let error = machine().receive(message, 1).unwrap_err();
        assert_eq!(
            error,
            ProtocolError::MalformedHello("supported versions are empty")
        );
    }

    #[test]
    fn rejects_incompatible_major_version_without_adding_peer() {
        let mut sender = machine();
        let mut message = sender.hello(0);
        let Some(Body::Hello(hello)) = message.body.as_mut() else {
            panic!("expected hello")
        };
        hello.supported_major_versions = vec![99];

        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        let response = receiver.receive(message, 1).unwrap().unwrap();
        assert!(matches!(response.body, Some(Body::NegotiationRejected(_))));
        assert!(receiver.peers().is_empty());
    }

    #[test]
    fn current_and_previous_minor_negotiate_previous_compatible_version() {
        let mut current = Machine::new("ump:machine:current", "current-session");
        let mut previous = Machine::new("ump:machine:previous", "previous-session")
            .with_maximum_minor(1)
            .unwrap();

        let current_hello = current.hello(1);
        assert_eq!(current_hello.protocol_minor, 2);
        let welcome = previous.receive(current_hello, 1).unwrap().unwrap();
        assert_eq!(welcome.protocol_minor, 1);
        assert!(matches!(
            welcome.body,
            Some(Body::Welcome(Welcome {
                selected_minor_version: 1,
                ..
            }))
        ));

        current.receive(welcome, 2).unwrap();
        assert_eq!(current.peers()["ump:machine:previous"].selected_minor, 1);
        assert_eq!(previous.peers()["ump:machine:current"].selected_minor, 1);
    }

    #[test]
    fn gateway_minor_floor_rejects_legacy_negotiation() {
        let mut gateway = Machine::new("ump:machine:gateway-arm", "gateway-session")
            .with_minimum_minor(2)
            .unwrap();
        let mut legacy = Machine::new("ump:machine:legacy", "legacy-session")
            .with_maximum_minor(1)
            .unwrap();
        let response = legacy.receive(gateway.hello(0), 1).unwrap().unwrap();
        assert!(matches!(response.body, Some(Body::NegotiationRejected(_))));
        assert!(legacy.peers().is_empty());
    }

    #[test]
    fn presence_expires_deterministically() {
        let mut sender = machine();
        let hello = sender.hello(0);
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        receiver.receive(hello, 10).unwrap();

        assert!(receiver.expire_peers(2_009).is_empty());
        assert_eq!(receiver.expire_peers(2_010), vec!["ump:machine:arm-1"]);
        assert_eq!(
            receiver.peers()["ump:machine:arm-1"].status,
            PeerStatus::Expired
        );
    }

    #[test]
    fn authenticated_identity_must_match_claimed_machine() {
        let mut sender = machine();
        let message = sender.hello(0);
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        let error = receiver
            .receive_authenticated(
                message,
                1,
                &AuthenticatedPeer::observer("ump:machine:intruder"),
            )
            .unwrap_err();
        assert!(matches!(error, ProtocolError::IdentityMismatch { .. }));
        assert!(receiver.peers().is_empty());
    }

    #[test]
    fn duplicate_message_cannot_refresh_presence() {
        let mut sender = machine();
        let hello = sender.hello(0);
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        receiver.receive(hello.clone(), 10).unwrap();
        let error = receiver.receive(hello, 1_000).unwrap_err();
        assert!(matches!(error, ProtocolError::DuplicateMessage(_)));
        assert_eq!(receiver.peers()[sender.machine_id()].last_seen_ms, 10);
    }

    #[test]
    fn heartbeat_replay_does_not_refresh_presence() {
        let mut sender = machine();
        let hello = sender.hello(0);
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        receiver.receive(hello, 1).unwrap();
        let heartbeat = sender.heartbeat(100);
        receiver.receive(heartbeat.clone(), 100).unwrap();
        let mut replay = heartbeat;
        replay.message_id = "replayed-under-new-envelope-id".into();
        let error = receiver.receive(replay, 1_000).unwrap_err();
        assert!(matches!(error, ProtocolError::Replay { .. }));
        assert_eq!(receiver.peers()[sender.machine_id()].last_seen_ms, 100);
    }

    #[test]
    fn metadata_requires_authorization_and_increasing_revision() {
        let mut sender = Machine::with_descriptor(
            "ump:machine:arm-1",
            "boot-arm-1",
            descriptor("robot_arm", 1),
        )
        .unwrap();
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        receiver.receive(sender.hello(0), 1).unwrap();

        let denied = AuthenticatedPeer {
            machine_id: sender.machine_id().into(),
            can_read_metadata: false,
            can_publish_metadata: false,
            can_issue_tasks: false,
            can_coordinate: false,
        };
        let error = receiver
            .receive_authenticated(sender.advertisement(2), 2, &denied)
            .unwrap_err();
        assert!(matches!(error, ProtocolError::Unauthorized(_)));
        assert!(receiver.peers()[sender.machine_id()].descriptor.is_none());

        let observer = AuthenticatedPeer::observer(sender.machine_id());
        receiver
            .receive_authenticated(sender.advertisement(3), 3, &observer)
            .unwrap();
        assert_eq!(
            receiver.peers()[sender.machine_id()]
                .descriptor
                .as_ref()
                .unwrap()
                .machine_class,
            "robot_arm"
        );
    }

    #[test]
    fn state_update_is_bounded_and_revisioned() {
        let mut machine = Machine::with_descriptor(
            "ump:machine:arm-1",
            "boot-arm-1",
            descriptor("robot_arm", 1),
        )
        .unwrap();
        machine
            .publish_state(StateUpdate {
                operational: OperationalState::Idle.into(),
                safety: SafetyState::Normal.into(),
                health: Vec::new(),
                telemetry: Vec::new(),
                revision: 1,
                source_time_ms: 100,
            })
            .unwrap();
        let error = machine.publish_state(machine.state().clone()).unwrap_err();
        assert!(matches!(error, ProtocolError::Replay { .. }));
    }

    #[test]
    fn authenticated_reconnect_replaces_the_peer_session() {
        let machine_id = "ump:machine:arm-1";
        let authenticated = AuthenticatedPeer::observer(machine_id);
        let mut first_session = Machine::new(machine_id, "boot-arm-1");
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        receiver
            .receive_authenticated(first_session.hello(0), 1, &authenticated)
            .unwrap();
        assert_eq!(receiver.peers()[machine_id].session_id, "boot-arm-1");

        let mut second_session = Machine::new(machine_id, "boot-arm-2");
        receiver
            .receive_authenticated(second_session.hello(100), 101, &authenticated)
            .unwrap();
        assert_eq!(receiver.peers()[machine_id].session_id, "boot-arm-2");

        let stale_heartbeat = first_session.heartbeat(200);
        let error = receiver
            .receive_authenticated(stale_heartbeat, 201, &authenticated)
            .unwrap_err();
        assert!(matches!(error, ProtocolError::Replay { .. }));
        assert_eq!(receiver.peers()[machine_id].session_id, "boot-arm-2");
    }

    #[test]
    fn task_messages_require_separate_issue_permission() {
        let mut sender = machine();
        let mut message = sender.hello(0);
        message.correlation_id = "correlation-task-denied".into();
        message.body = Some(Body::TaskRequest(TaskRequest::default()));
        let denied = AuthenticatedPeer {
            machine_id: sender.machine_id().into(),
            can_read_metadata: true,
            can_publish_metadata: true,
            can_issue_tasks: false,
            can_coordinate: false,
        };
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        assert!(matches!(
            receiver.receive_authenticated(message, 1, &denied),
            Err(ProtocolError::Unauthorized(_))
        ));
    }

    #[test]
    fn coordination_messages_require_separate_permission() {
        let mut sender = machine();
        let mut message = sender.hello(0);
        message.correlation_id = "correlation-reservation-denied".into();
        message.body = Some(Body::ReservationRequest(ReservationRequest::default()));
        let denied = AuthenticatedPeer {
            machine_id: sender.machine_id().into(),
            can_read_metadata: true,
            can_publish_metadata: true,
            can_issue_tasks: true,
            can_coordinate: false,
        };
        let mut receiver = Machine::new("ump:machine:base-1", "boot-base-1");
        assert!(matches!(
            receiver.receive_authenticated(message, 1, &denied),
            Err(ProtocolError::Unauthorized(_))
        ));
    }
}
