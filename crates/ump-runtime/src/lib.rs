#![forbid(unsafe_code)]

use std::collections::BTreeMap;

use thiserror::Error;
use ump_protocol::v1::{Envelope, Heartbeat, Hello, NegotiationRejected, Welcome, envelope::Body};

pub const PROTOCOL_MAJOR: u32 = 1;
pub const PROTOCOL_MINOR: u32 = 0;
pub const DEFAULT_PRESENCE_TTL_MS: u64 = 2_000;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PeerStatus {
    Present,
    Expired,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Peer {
    pub machine_id: String,
    pub session_id: String,
    pub selected_major: u32,
    pub selected_minor: u32,
    pub last_seen_ms: u64,
    pub expires_at_ms: u64,
    pub status: PeerStatus,
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
}

#[derive(Debug)]
pub struct Machine {
    machine_id: String,
    session_id: String,
    message_sequence: u64,
    heartbeat_sequence: u64,
    peers: BTreeMap<String, Peer>,
}

impl Machine {
    pub fn new(machine_id: impl Into<String>, session_id: impl Into<String>) -> Self {
        Self {
            machine_id: machine_id.into(),
            session_id: session_id.into(),
            message_sequence: 0,
            heartbeat_sequence: 0,
            peers: BTreeMap::new(),
        }
    }

    pub fn machine_id(&self) -> &str {
        &self.machine_id
    }

    pub fn peers(&self) -> &BTreeMap<String, Peer> {
        &self.peers
    }

    pub fn hello(&mut self, now_ms: u64) -> Envelope {
        let body = Body::Hello(Hello {
            supported_major_versions: vec![PROTOCOL_MAJOR],
            minimum_minor_version: 0,
            maximum_minor_version: PROTOCOL_MINOR,
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

    pub fn receive(
        &mut self,
        envelope: Envelope,
        now_ms: u64,
    ) -> Result<Option<Envelope>, ProtocolError> {
        validate_envelope(&envelope, now_ms)?;
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

                if hello.minimum_minor_version > PROTOCOL_MINOR {
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
                let selected_minor = PROTOCOL_MINOR;

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
                    peer.last_seen_ms = now_ms;
                    peer.expires_at_ms = now_ms + heartbeat.presence_ttl_ms;
                    peer.status = PeerStatus::Present;
                }
                Ok(None)
            }
            Some(Body::Welcome(welcome)) => {
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
            Some(Body::NegotiationRejected(_)) | None => Ok(None),
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
            protocol_minor: PROTOCOL_MINOR,
            message_id: format!("{}-{}", self.session_id, self.message_sequence),
            source_machine_id: self.machine_id.clone(),
            source_session_id: self.session_id.clone(),
            sent_at_ms: now_ms,
            expires_at_ms,
            body: Some(body),
        }
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
    if let Some(expires_at_ms) = envelope.expires_at_ms
        && now_ms >= expires_at_ms
    {
        return Err(ProtocolError::Expired {
            message_id: envelope.message_id.clone(),
            expires_at_ms,
        });
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

#[cfg(test)]
mod tests {
    use super::*;

    fn machine() -> Machine {
        Machine::new("ump:machine:arm-1", "boot-arm-1")
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
}
