use serde::Serialize;
use ump_runtime::{DEFAULT_PRESENCE_TTL_MS, Machine, PROTOCOL_MAJOR, PROTOCOL_MINOR, PeerStatus};
use ump_sim::Simulation;

const ARM: &str = "ump:machine:arm-1";
const BASE: &str = "ump:machine:base-1";

#[derive(Serialize)]
struct Check {
    name: &'static str,
    passed: bool,
}

#[derive(Serialize)]
struct Report {
    scenario: &'static str,
    protocol: String,
    simulated_duration_ms: u64,
    passed: bool,
    checks: Vec<Check>,
    events: Vec<String>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut simulation = Simulation::default();
    simulation.add_machine(Machine::new(ARM, "session-arm-1"));
    simulation.add_machine(Machine::new(BASE, "session-base-1"));
    simulation.exchange_hellos(ARM, BASE)?;

    let negotiated = [ARM, BASE].iter().all(|machine_id| {
        simulation.machine(machine_id).is_some_and(|machine| {
            machine.peers().values().all(|peer| {
                peer.selected_major == PROTOCOL_MAJOR
                    && peer.selected_minor == PROTOCOL_MINOR
                    && peer.status == PeerStatus::Present
            })
        })
    });

    simulation.advance_to(DEFAULT_PRESENCE_TTL_MS);
    let expired = [ARM, BASE].iter().all(|machine_id| {
        simulation.machine(machine_id).is_some_and(|machine| {
            machine
                .peers()
                .values()
                .all(|peer| peer.status == PeerStatus::Expired)
        })
    });

    let checks = vec![
        Check {
            name: "mutual version negotiation",
            passed: negotiated,
        },
        Check {
            name: "deterministic presence expiry",
            passed: expired,
        },
    ];
    let passed = checks.iter().all(|check| check.passed);
    let report = Report {
        scenario: "S0 two virtual machines say hello",
        protocol: format!("{PROTOCOL_MAJOR}.{PROTOCOL_MINOR}"),
        simulated_duration_ms: simulation.now_ms(),
        passed,
        checks,
        events: simulation.events().to_vec(),
    };

    println!("{}", serde_json::to_string_pretty(&report)?);
    if passed {
        Ok(())
    } else {
        Err("S0 simulation failed".into())
    }
}
