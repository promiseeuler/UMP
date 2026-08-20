#![forbid(unsafe_code)]

use std::collections::{BTreeMap, VecDeque};

use thiserror::Error;
use ump_protocol::v1::Envelope;
use ump_runtime::{Machine, ProtocolError};

#[derive(Debug)]
struct Delivery {
    target: String,
    envelope: Envelope,
}

#[derive(Debug, Error)]
pub enum SimulationError {
    #[error("unknown simulated machine: {0}")]
    UnknownMachine(String),
    #[error("protocol error at {machine}: {source}")]
    Protocol {
        machine: String,
        #[source]
        source: ProtocolError,
    },
    #[error("credential for {machine} expired at {expires_at_ms}")]
    CredentialExpired { machine: String, expires_at_ms: u64 },
}

#[derive(Debug, Default)]
pub struct Simulation {
    now_ms: u64,
    machines: BTreeMap<String, Machine>,
    deliveries: VecDeque<Delivery>,
    events: Vec<String>,
    credential_expiry: BTreeMap<String, u64>,
}

impl Simulation {
    pub fn add_machine(&mut self, machine: Machine) {
        self.add_machine_with_credential(machine, u64::MAX);
    }

    pub fn add_machine_with_credential(&mut self, machine: Machine, expires_at_ms: u64) {
        let machine_id = machine.machine_id().to_owned();
        self.credential_expiry
            .insert(machine_id.clone(), expires_at_ms);
        self.machines.insert(machine_id, machine);
    }

    pub fn now_ms(&self) -> u64 {
        self.now_ms
    }

    pub fn machine(&self, machine_id: &str) -> Option<&Machine> {
        self.machines.get(machine_id)
    }

    pub fn machine_mut(&mut self, machine_id: &str) -> Option<&mut Machine> {
        self.machines.get_mut(machine_id)
    }

    pub fn events(&self) -> &[String] {
        &self.events
    }

    pub fn exchange_hellos(&mut self, left: &str, right: &str) -> Result<(), SimulationError> {
        let left_hello = self
            .machines
            .get_mut(left)
            .ok_or_else(|| SimulationError::UnknownMachine(left.into()))?
            .hello(self.now_ms);
        let right_hello = self
            .machines
            .get_mut(right)
            .ok_or_else(|| SimulationError::UnknownMachine(right.into()))?
            .hello(self.now_ms);
        self.send(right, left_hello);
        self.send(left, right_hello);
        self.deliver_all()
    }

    pub fn send_heartbeat(&mut self, source: &str, target: &str) -> Result<(), SimulationError> {
        let heartbeat = self
            .machines
            .get_mut(source)
            .ok_or_else(|| SimulationError::UnknownMachine(source.into()))?
            .heartbeat(self.now_ms);
        self.send(target, heartbeat);
        self.deliver_all()
    }

    pub fn deliver(&mut self, target: &str, envelope: Envelope) -> Result<(), SimulationError> {
        self.send(target, envelope);
        self.deliver_all()
    }

    pub fn advance_to(&mut self, now_ms: u64) {
        assert!(now_ms >= self.now_ms, "simulated time cannot move backward");
        self.now_ms = now_ms;
        for machine in self.machines.values_mut() {
            for peer in machine.expire_peers(now_ms) {
                self.events.push(format!(
                    "t={now_ms} {} expired {peer}",
                    machine.machine_id()
                ));
            }
        }
    }

    fn send(&mut self, target: &str, envelope: Envelope) {
        self.events.push(format!(
            "t={} {} -> {target} {}",
            self.now_ms, envelope.source_machine_id, envelope.message_id
        ));
        self.deliveries.push_back(Delivery {
            target: target.into(),
            envelope,
        });
    }

    fn deliver_all(&mut self) -> Result<(), SimulationError> {
        while let Some(delivery) = self.deliveries.pop_front() {
            let source = delivery.envelope.source_machine_id.clone();
            let credential_expiry = self
                .credential_expiry
                .get(&source)
                .copied()
                .ok_or_else(|| SimulationError::UnknownMachine(source.clone()))?;
            if self.now_ms >= credential_expiry {
                self.events.push(format!(
                    "t={} rejected expired credential for {source}",
                    self.now_ms
                ));
                return Err(SimulationError::CredentialExpired {
                    machine: source,
                    expires_at_ms: credential_expiry,
                });
            }
            let authenticated = ump_runtime::AuthenticatedPeer::observer(&source);
            let response = self
                .machines
                .get_mut(&delivery.target)
                .ok_or_else(|| SimulationError::UnknownMachine(delivery.target.clone()))?
                .receive_authenticated(delivery.envelope, self.now_ms, &authenticated)
                .map_err(|source| SimulationError::Protocol {
                    machine: delivery.target.clone(),
                    source,
                })?;
            if let Some(response) = response {
                self.send(&source, response);
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ump_runtime::{DEFAULT_PRESENCE_TTL_MS, PeerStatus};

    const ARM: &str = "ump:machine:arm-1";
    const BASE: &str = "ump:machine:base-1";

    fn two_machine_simulation() -> Simulation {
        let mut sim = Simulation::default();
        sim.add_machine(Machine::new(ARM, "session-arm-1"));
        sim.add_machine(Machine::new(BASE, "session-base-1"));
        sim
    }

    #[test]
    fn machines_negotiate_and_expire_at_the_same_simulated_time() {
        let mut sim = two_machine_simulation();
        sim.exchange_hellos(ARM, BASE).unwrap();

        assert_eq!(
            sim.machine(ARM).unwrap().peers()[BASE].status,
            PeerStatus::Present
        );
        assert_eq!(
            sim.machine(BASE).unwrap().peers()[ARM].status,
            PeerStatus::Present
        );

        sim.advance_to(DEFAULT_PRESENCE_TTL_MS - 1);
        assert_eq!(
            sim.machine(ARM).unwrap().peers()[BASE].status,
            PeerStatus::Present
        );

        sim.advance_to(DEFAULT_PRESENCE_TTL_MS);
        assert_eq!(
            sim.machine(ARM).unwrap().peers()[BASE].status,
            PeerStatus::Expired
        );
        assert_eq!(
            sim.machine(BASE).unwrap().peers()[ARM].status,
            PeerStatus::Expired
        );
    }

    #[test]
    fn heartbeat_renews_only_the_receivers_view() {
        let mut sim = two_machine_simulation();
        sim.exchange_hellos(ARM, BASE).unwrap();
        sim.advance_to(1_000);
        sim.send_heartbeat(ARM, BASE).unwrap();
        sim.advance_to(2_000);

        assert_eq!(
            sim.machine(ARM).unwrap().peers()[BASE].status,
            PeerStatus::Expired
        );
        assert_eq!(
            sim.machine(BASE).unwrap().peers()[ARM].status,
            PeerStatus::Present
        );
    }
}
