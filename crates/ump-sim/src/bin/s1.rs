use std::{process::Command, time::Instant};

use serde::Serialize;
use ump_protocol::v1::{
    Capability, CapabilityAvailability, CapabilityConstraint, Endpoint, Feature, HealthCondition,
    MachineDescriptor, OperationalState, SafetyState, StateUpdate, TelemetrySample, TransportKind,
    envelope::Body,
};
use ump_runtime::{DEFAULT_PRESENCE_TTL_MS, Machine, PeerStatus, ProtocolError};
use ump_sim::{Simulation, SimulationError};

const CLASSES: [&str; 4] = ["mobile_base", "robot_arm", "drone", "fixed_sensor"];
const MACHINES_PER_CLASS: usize = 5;
const OBSERVER: &str = "ump:machine:fixed_sensor-0";
const DISAPPEARING: &str = "ump:machine:drone-4";

#[derive(Serialize)]
struct Check {
    name: &'static str,
    passed: bool,
    evidence: String,
}

#[derive(Serialize)]
struct Report {
    scenario: &'static str,
    machine_count: usize,
    machine_classes: usize,
    discovery_convergence_ms: u64,
    compatible_negotiations: usize,
    incompatible_negotiations_rejected: usize,
    duplicate_packets_rejected: usize,
    expired_credentials_rejected: usize,
    stale_state_updates_rejected: usize,
    telemetry_flood_samples_rejected: usize,
    false_live_transitions: usize,
    false_offline_transitions: usize,
    resident_memory_kib: Option<u64>,
    process_cpu_percent: Option<f64>,
    wall_duration_ms: u64,
    event_count: usize,
    passed: bool,
    checks: Vec<Check>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let wall_started = Instant::now();
    let mut simulation = Simulation::default();
    let all_ids = machine_ids();
    let initial_ids = &all_ids[..16];
    let delayed_ids = &all_ids[16..];

    for machine_id in initial_ids {
        simulation.add_machine(make_machine(machine_id)?);
    }
    exchange_all(&mut simulation, initial_ids)?;

    simulation.advance_to(500);
    for machine_id in delayed_ids {
        simulation.add_machine(make_machine(machine_id)?);
    }
    for delayed in delayed_ids {
        for existing in initial_ids {
            simulation.exchange_hellos(delayed, existing)?;
        }
    }
    exchange_all(&mut simulation, delayed_ids)?;

    let all_discovered = all_ids.iter().all(|machine_id| {
        simulation
            .machine(machine_id)
            .is_some_and(|machine| machine.peers().len() == all_ids.len() - 1)
    });

    for machine_id in all_ids.iter().filter(|id| id.as_str() != OBSERVER) {
        let advertisement = simulation
            .machine_mut(machine_id)
            .expect("machine exists")
            .advertisement(500);
        simulation.deliver(OBSERVER, advertisement)?;
    }
    let heterogeneous_metadata = simulation.machine(OBSERVER).is_some_and(|observer| {
        let mut classes = observer
            .peers()
            .values()
            .filter_map(|peer| peer.descriptor.as_ref())
            .map(|descriptor| descriptor.machine_class.as_str())
            .collect::<Vec<_>>();
        classes.sort_unstable();
        classes.dedup();
        classes.len() == CLASSES.len()
            && observer.peers().values().all(|peer| {
                peer.descriptor
                    .as_ref()
                    .is_some_and(|descriptor| !descriptor.capabilities.is_empty())
            })
    });

    let duplicate_packets_rejected = duplicate_packet_fault()? as usize;
    let incompatible_negotiations_rejected = incompatible_version_fault()? as usize;
    let expired_credentials_rejected = expired_credential_fault()? as usize;
    let (stale_state_updates_rejected, telemetry_flood_samples_rejected) =
        state_faults(&mut simulation)?;

    simulation.advance_to(2_000);
    for machine_id in all_ids
        .iter()
        .filter(|id| id.as_str() != OBSERVER && id.as_str() != DISAPPEARING)
    {
        simulation.send_heartbeat(machine_id, OBSERVER)?;
    }
    simulation.advance_to(500 + DEFAULT_PRESENCE_TTL_MS);
    let observer = simulation.machine(OBSERVER).expect("observer exists");
    let expired_at_observer = observer
        .peers()
        .values()
        .filter(|peer| peer.status == PeerStatus::Expired)
        .map(|peer| peer.machine_id.as_str())
        .collect::<Vec<_>>();
    let disappearance_correct = expired_at_observer == vec![DISAPPEARING];
    let false_live_transitions =
        usize::from(observer.peers()[DISAPPEARING].status != PeerStatus::Expired);
    let false_offline_transitions = observer
        .peers()
        .values()
        .filter(|peer| peer.machine_id != DISAPPEARING && peer.status != PeerStatus::Present)
        .count();

    let checks = vec![
        Check {
            name: "20 machines across four classes converge",
            passed: all_discovered,
            evidence: "all machines hold 19 negotiated peers by simulated t=500 ms".into(),
        },
        Check {
            name: "capabilities and revisions are advertised",
            passed: heterogeneous_metadata,
            evidence: "observer stores protected descriptors for every peer".into(),
        },
        Check {
            name: "duplicate packet is rejected",
            passed: duplicate_packets_rejected == 1,
            evidence: "duplicate message identifier does not refresh presence".into(),
        },
        Check {
            name: "unsupported major version is rejected",
            passed: incompatible_negotiations_rejected == 1,
            evidence: "incompatible source is not added as a peer".into(),
        },
        Check {
            name: "expired credential is rejected",
            passed: expired_credentials_rejected == 1,
            evidence: "authenticated delivery fails before protocol state changes".into(),
        },
        Check {
            name: "stale state is rejected",
            passed: stale_state_updates_rejected == 1,
            evidence: "equal state revision cannot replace stored state".into(),
        },
        Check {
            name: "telemetry flood is backpressured",
            passed: telemetry_flood_samples_rejected == 257,
            evidence: "oversized telemetry batch is rejected before storage".into(),
        },
        Check {
            name: "disappearing peer expires without false transitions",
            passed: disappearance_correct
                && false_live_transitions == 0
                && false_offline_transitions == 0,
            evidence: "only drone-4 expires in observer view".into(),
        },
    ];
    let passed = checks.iter().all(|check| check.passed);
    let report = Report {
        scenario: "S1 heterogeneous discovery lab",
        machine_count: all_ids.len(),
        machine_classes: CLASSES.len(),
        discovery_convergence_ms: 500,
        compatible_negotiations: all_ids.len() * (all_ids.len() - 1) / 2,
        incompatible_negotiations_rejected,
        duplicate_packets_rejected,
        expired_credentials_rejected,
        stale_state_updates_rejected,
        telemetry_flood_samples_rejected,
        false_live_transitions,
        false_offline_transitions,
        resident_memory_kib: resident_memory_kib(),
        process_cpu_percent: process_cpu_percent(),
        wall_duration_ms: wall_started.elapsed().as_millis() as u64,
        event_count: simulation.events().len(),
        passed,
        checks,
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    if passed {
        Ok(())
    } else {
        Err("S1 simulation failed".into())
    }
}

fn machine_ids() -> Vec<String> {
    CLASSES
        .iter()
        .flat_map(|class| {
            (0..MACHINES_PER_CLASS).map(move |index| format!("ump:machine:{class}-{index}"))
        })
        .collect()
}

fn make_machine(machine_id: &str) -> Result<Machine, ProtocolError> {
    let short_id = machine_id
        .rsplit(':')
        .next()
        .expect("machine ID has suffix");
    let machine_class = short_id
        .rsplit_once('-')
        .map_or(short_id, |(class, _)| class);
    let index = short_id
        .rsplit_once('-')
        .and_then(|(_, index)| index.parse::<u64>().ok())
        .unwrap_or(0);
    let capability_type = match machine_class {
        "mobile_base" => "org.ump.mobility.transport",
        "robot_arm" => "org.ump.material.pick",
        "drone" => "org.ump.inspection.aerial",
        _ => "org.ump.sensing.zone_occupancy",
    };
    let descriptor = MachineDescriptor {
        machine_class: machine_class.into(),
        manufacturer: format!("Virtual Vendor {}", index % 3),
        model: format!("{machine_class}-sim-{index}"),
        software_version: "0.1.0".into(),
        endpoints: vec![Endpoint {
            uri: format!("quic://{short_id}.ump.local:7443"),
            transport: TransportKind::Quic.into(),
            priority: 1,
        }],
        features: vec![Feature {
            name: format!("org.ump.feature.{machine_class}"),
            version: format!("1.{}", index % 2),
            required: false,
        }],
        capabilities: vec![Capability {
            r#type: capability_type.into(),
            version: format!("1.{}", index % 3),
            label: capability_type
                .rsplit('.')
                .next()
                .unwrap_or("capability")
                .into(),
            input_schema_uri: format!("ump://schemas/{machine_class}/input/1"),
            output_schema_uri: format!("ump://schemas/{machine_class}/output/1"),
            availability: CapabilityAvailability::Available.into(),
            observable: true,
            invocable: machine_class != "fixed_sensor",
            reservable: machine_class != "fixed_sensor",
            interruptible: true,
            handoff_capable: matches!(machine_class, "mobile_base" | "robot_arm"),
            revision: index + 1,
            constraints: vec![CapabilityConstraint {
                name: "nominal_capacity".into(),
                minimum: 0.0,
                maximum: 10.0 + index as f64,
                unit: "unit".into(),
            }],
        }],
        revision: index + 1,
        deployment_mode: ump_protocol::v1::DeploymentMode::Direct.into(),
        proxy: None,
    };
    Machine::with_descriptor(machine_id, format!("session-{short_id}"), descriptor)
}

fn exchange_all(simulation: &mut Simulation, ids: &[String]) -> Result<(), SimulationError> {
    for left in 0..ids.len() {
        for right in (left + 1)..ids.len() {
            simulation.exchange_hellos(&ids[left], &ids[right])?;
        }
    }
    Ok(())
}

fn duplicate_packet_fault() -> Result<bool, Box<dyn std::error::Error>> {
    let mut simulation = Simulation::default();
    let source = "ump:machine:robot_arm-duplicate";
    let target = "ump:machine:mobile_base-duplicate";
    simulation.add_machine(make_machine(source)?);
    simulation.add_machine(make_machine(target)?);
    let hello = simulation.machine_mut(source).unwrap().hello(0);
    simulation.deliver(target, hello.clone())?;
    Ok(matches!(
        simulation.deliver(target, hello),
        Err(SimulationError::Protocol {
            source: ProtocolError::DuplicateMessage(_),
            ..
        })
    ))
}

fn incompatible_version_fault() -> Result<bool, Box<dyn std::error::Error>> {
    let mut simulation = Simulation::default();
    let source = "ump:machine:drone-incompatible";
    let target = "ump:machine:fixed_sensor-version";
    simulation.add_machine(make_machine(source)?);
    simulation.add_machine(make_machine(target)?);
    let mut hello = simulation.machine_mut(source).unwrap().hello(0);
    let Some(Body::Hello(body)) = hello.body.as_mut() else {
        return Err("expected hello".into());
    };
    body.supported_major_versions = vec![99];
    simulation.deliver(target, hello)?;
    Ok(simulation.machine(target).unwrap().peers().is_empty())
}

fn expired_credential_fault() -> Result<bool, Box<dyn std::error::Error>> {
    let mut simulation = Simulation::default();
    let source = "ump:machine:robot_arm-expired";
    let target = "ump:machine:fixed_sensor-credential";
    simulation.add_machine_with_credential(make_machine(source)?, 100);
    simulation.add_machine(make_machine(target)?);
    simulation.advance_to(100);
    let hello = simulation.machine_mut(source).unwrap().hello(100);
    Ok(matches!(
        simulation.deliver(target, hello),
        Err(SimulationError::CredentialExpired { .. })
    ))
}

fn state_faults(simulation: &mut Simulation) -> Result<(usize, usize), Box<dyn std::error::Error>> {
    let source = "ump:machine:robot_arm-0";
    let state = StateUpdate {
        operational: OperationalState::Idle.into(),
        safety: SafetyState::Normal.into(),
        health: vec![HealthCondition {
            component: "controller".into(),
            severity: ump_protocol::v1::HealthSeverity::Info.into(),
            code: "ump.health.ready".into(),
            message: "ready".into(),
            first_seen_ms: 500,
            remediation: String::new(),
        }],
        telemetry: Vec::new(),
        revision: 1,
        source_time_ms: 500,
    };
    simulation
        .machine_mut(source)
        .unwrap()
        .publish_state(state)?;
    let update = simulation.machine_mut(source).unwrap().state_update(500);
    simulation.deliver(OBSERVER, update)?;
    let stale = simulation.machine_mut(source).unwrap().state_update(501);
    let stale_rejected = matches!(
        simulation.deliver(OBSERVER, stale),
        Err(SimulationError::Protocol {
            source: ProtocolError::Replay { .. },
            ..
        })
    ) as usize;

    let mut flood = simulation.machine_mut(source).unwrap().state_update(502);
    let Some(Body::StateUpdate(body)) = flood.body.as_mut() else {
        return Err("expected state update".into());
    };
    body.revision = 2;
    body.telemetry = (0..257)
        .map(|sequence| TelemetrySample {
            metric: "joint.temperature".into(),
            value: 40.0,
            unit: "Cel".into(),
            source_time_ms: 502,
            sequence,
        })
        .collect();
    let flood_rejected = matches!(
        simulation.deliver(OBSERVER, flood),
        Err(SimulationError::Protocol {
            source: ProtocolError::TelemetryLimit { .. },
            ..
        })
    );
    Ok((stale_rejected, if flood_rejected { 257 } else { 0 }))
}

fn command_output(program: &str, arguments: &[&str]) -> String {
    Command::new(program)
        .args(arguments)
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_default()
}

fn resident_memory_kib() -> Option<u64> {
    let pid = std::process::id().to_string();
    command_output("ps", &["-o", "rss=", "-p", &pid])
        .parse()
        .ok()
}

fn process_cpu_percent() -> Option<f64> {
    let pid = std::process::id().to_string();
    command_output("ps", &["-o", "%cpu=", "-p", &pid])
        .parse()
        .ok()
}
