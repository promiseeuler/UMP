#![forbid(unsafe_code)]

use std::{
    collections::BTreeMap,
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
#[cfg(unix)]
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader as AsyncBufReader},
    net::{UnixListener, UnixStream},
};
use ump_protocol::v1 as wire;
use ump_protocol::v1::{
    Capability, CapabilityAvailability, Endpoint, Envelope, IdempotencyPolicy, MachineDescriptor,
    OperationalState, SafetyState, StateUpdate, TaskAcknowledgement, TaskReconcileResponse,
    TaskSnapshot, TaskStatus, TransportKind, envelope::Body,
};
use ump_runtime::coordination::{
    Claim as NativeClaim, Concurrency as NativeConcurrency, CoordinationEngine,
    CoordinationJournal, Evidence as NativeEvidence, FileCoordinationJournal,
    Handoff as NativeHandoff, HandoffState as NativeHandoffState, Reservation as NativeReservation,
    ReservationState as NativeReservationState, Resource as NativeResource,
    SpatialContext as NativeSpatialContext,
};
use ump_runtime::task::{FileJournal, Journal, Lease, LeaseStatus, TaskEngine};
use ump_runtime::task::{NewTask, RetryPolicy, TaskState};
use ump_runtime::{AuthenticatedPeer, Machine, PeerStatus};
use ump_transport::{
    credentials::{
        DevelopmentPki, certificate_fingerprint, client_config_from_der,
        server_config_from_der_roots,
    },
    framing::{read_envelope, write_envelope},
    quic::{client_endpoint, peer_certificate_fingerprint, server_endpoint},
};

const CONFIG_FILE: &str = "config.json";
const ROOT_CERT_FILE: &str = "development-root.der";
const IDENTITY_CERT_FILE: &str = "identity.der";
const IDENTITY_KEY_FILE: &str = "identity-key.der";
const CREDENTIAL_ARCHIVE_DIR: &str = "credential-archive";
const STATE_FILE: &str = "runtime-state.json";
const TRUSTED_CERTIFICATES_DIR: &str = "trusted-certificates";
const TASK_JOURNAL_FILE: &str = "task-journal.jsonl";
const COORDINATION_JOURNAL_FILE: &str = "coordination-journal.jsonl";
const ADAPTER_SOCKET_FILE: &str = "adapter.sock";
const MAX_ADAPTER_REQUEST_BYTES: usize = 96 * 1024;

fn default_protocol_minor() -> u32 {
    ump_runtime::PROTOCOL_MINOR
}

fn default_protocol_minimum_minor() -> u32 {
    0
}

#[derive(Parser)]
#[command(name = "ump", version, about = "Universal Machine Protocol tools")]
pub struct Cli {
    #[arg(long, global = true, default_value = ".ump")]
    pub data_dir: PathBuf,
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand)]
pub enum Command {
    Init {
        #[arg(long)]
        machine_id: String,
        #[arg(long)]
        machine_class: String,
        #[arg(long)]
        dns_name: Option<String>,
        #[arg(long, default_value = "127.0.0.1:7443")]
        listen: SocketAddr,
        #[arg(long, default_value_t = ump_runtime::PROTOCOL_MINOR)]
        protocol_minor: u32,
        #[arg(long)]
        force: bool,
    },
    Run {
        #[arg(long)]
        once: bool,
    },
    RotateDevelopmentCredentials {
        #[arg(long)]
        confirm_machine_id: String,
    },
    Peers,
    Inspect {
        machine_id: Option<String>,
    },
    Inspector {
        #[arg(long)]
        output: Option<PathBuf>,
    },
    Doctor,
    Tasks,
    Timeline {
        task_id: String,
    },
    IssueTask {
        #[arg(long)]
        target: SocketAddr,
        #[arg(long)]
        target_name: String,
        #[arg(long)]
        target_machine: String,
        #[arg(long)]
        task_id: String,
        #[arg(long)]
        capability: String,
        #[arg(long)]
        input_json: String,
        #[arg(long)]
        lease_id: String,
        #[arg(long)]
        idempotency_key: String,
        #[arg(long, default_value_t = 30_000)]
        deadline_after_ms: u64,
        #[arg(long, default_value_t = 1)]
        maximum_attempts: u32,
        #[arg(long, value_enum, default_value_t = IdempotencyArg::AtMostOnce)]
        idempotency: IdempotencyArg,
        #[arg(long)]
        correlation_id: Option<String>,
        #[arg(long)]
        wait: bool,
    },
    QueryTask {
        #[arg(long)]
        target: SocketAddr,
        #[arg(long)]
        target_name: String,
        #[arg(long)]
        target_machine: String,
        #[arg(long)]
        task_id: String,
    },
    CancelTask {
        #[arg(long)]
        target: SocketAddr,
        #[arg(long)]
        target_name: String,
        #[arg(long)]
        target_machine: String,
        #[arg(long)]
        task_id: String,
        #[arg(long, default_value = "operator_requested")]
        reason: String,
    },
    ReserveResource {
        #[arg(long)]
        target: SocketAddr,
        #[arg(long)]
        target_name: String,
        #[arg(long)]
        target_machine: String,
        #[arg(long)]
        reservation_id: String,
        #[arg(long)]
        task_id: String,
        #[arg(long)]
        resource_id: String,
        #[arg(long, default_value_t = 1)]
        quantity: u32,
        #[arg(long, default_value_t = 120_000)]
        duration_ms: u64,
    },
    ReleaseResource {
        #[arg(long)]
        target: SocketAddr,
        #[arg(long)]
        target_name: String,
        #[arg(long)]
        target_machine: String,
        #[arg(long)]
        reservation_id: String,
        #[arg(long)]
        previous_revision: u64,
        #[arg(long, default_value = "mission_complete")]
        reason: String,
    },
    ProposeHandoff {
        #[arg(long)]
        target: SocketAddr,
        #[arg(long)]
        target_name: String,
        #[arg(long)]
        target_machine: String,
        #[arg(long)]
        handoff_id: String,
        #[arg(long)]
        task_id: String,
        #[arg(long)]
        destination: String,
        #[arg(long)]
        subject_id: String,
        #[arg(long)]
        reservation_id: String,
        #[arg(long)]
        reference_frame: String,
        #[arg(long)]
        subject_frame: String,
        #[arg(long)]
        x: f64,
        #[arg(long)]
        y: f64,
        #[arg(long)]
        z: f64,
        #[arg(long, default_value_t = 0.02)]
        position_uncertainty_m: f64,
        #[arg(long, default_value_t = 0.05)]
        orientation_uncertainty_rad: f64,
        #[arg(long, default_value_t = 30_000)]
        maximum_age_ms: u64,
        #[arg(long, default_value_t = 120_000)]
        deadline_after_ms: u64,
    },
    UpdateHandoff {
        #[arg(long)]
        target: SocketAddr,
        #[arg(long)]
        target_name: String,
        #[arg(long)]
        target_machine: String,
        #[arg(long)]
        handoff_id: String,
        #[arg(long, value_enum)]
        state: HandoffStateArg,
        #[arg(long)]
        previous_revision: u64,
        #[arg(long)]
        evidence_type: Option<String>,
        #[arg(long)]
        evidence: Option<String>,
        #[arg(long, default_value = "")]
        reason: String,
    },
    QueryHandoff {
        #[arg(long)]
        target: SocketAddr,
        #[arg(long)]
        target_name: String,
        #[arg(long)]
        target_machine: String,
        #[arg(long)]
        handoff_id: String,
    },
    GrantLease {
        #[arg(long)]
        lease_id: String,
        #[arg(long)]
        holder: String,
        #[arg(long)]
        capability: Vec<String>,
        #[arg(long)]
        resource: Vec<String>,
        #[arg(long, default_value_t = 60_000)]
        duration_ms: u64,
        #[arg(long, default_value_t = 100)]
        clock_uncertainty_ms: u64,
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        renewable: bool,
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        exclusive: bool,
    },
    RegisterCapability {
        #[arg(long)]
        capability: String,
        #[arg(long)]
        interruptible: bool,
    },
    RegisterResource {
        #[arg(long)]
        resource_id: String,
        #[arg(long)]
        resource_type: String,
        #[arg(long, value_enum)]
        concurrency: ResourceConcurrencyArg,
        #[arg(long, default_value_t = 1)]
        capacity: u32,
        #[arg(long)]
        frame_id: Option<String>,
    },
    Resources,
    Reservations,
    Handoffs,
    CoordinationTimeline {
        correlation_id: String,
    },
    Trust {
        #[arg(long)]
        machine_id: String,
        #[arg(long)]
        certificate: PathBuf,
        #[arg(long)]
        root_certificate: PathBuf,
        #[arg(long)]
        allow_metadata: bool,
        #[arg(long)]
        allow_tasks: bool,
        #[arg(long)]
        allow_coordination: bool,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Config {
    pub machine_id: String,
    pub machine_class: String,
    pub dns_name: String,
    pub listen: SocketAddr,
    pub development_credentials: bool,
    #[serde(default = "default_protocol_minor")]
    pub protocol_minor: u32,
    #[serde(default = "default_protocol_minimum_minor")]
    pub protocol_minimum_minor: u32,
    pub trusted_peers: BTreeMap<String, TrustedPeer>,
    #[serde(default)]
    pub task_capabilities: BTreeMap<String, CapabilityConfig>,
    #[serde(default)]
    pub gateway_proxy: Option<GatewayProxyConfig>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GatewayProxyConfig {
    pub gateway_id: String,
    pub represented_machine_id: String,
    pub controller_interface: String,
    #[serde(default)]
    pub read_only: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CapabilityConfig {
    pub interruptible: bool,
}

#[derive(Clone, Copy, Debug, clap::ValueEnum)]
pub enum ResourceConcurrencyArg {
    Exclusive,
    Shared,
    Capacity,
}

#[derive(Clone, Copy, Debug, clap::ValueEnum)]
pub enum IdempotencyArg {
    SafeToRetry,
    AtMostOnce,
    ReconcileRequired,
}

#[derive(Clone, Copy, Debug, clap::ValueEnum)]
pub enum HandoffStateArg {
    Prepared,
    Ready,
    Transferring,
    Committed,
    Aborted,
    Failed,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TrustedPeer {
    pub machine_id: String,
    pub can_read_metadata: bool,
    pub can_publish_metadata: bool,
    #[serde(default)]
    pub can_issue_tasks: bool,
    #[serde(default)]
    pub can_coordinate: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct PeerSnapshot {
    machine_id: String,
    session_id: String,
    status: String,
    selected_major: u32,
    selected_minor: u32,
    machine_class: Option<String>,
    capability_count: usize,
    state_revision: Option<u64>,
    #[serde(default = "default_direct_deployment")]
    deployment_mode: String,
    #[serde(default)]
    proxy: Option<ProxySnapshot>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct ProxySnapshot {
    gateway_id: String,
    represented_machine_id: String,
    controller_interface: String,
    read_only: bool,
}

fn default_direct_deployment() -> String {
    "direct".into()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct HealthSnapshot {
    component: String,
    severity: String,
    code: String,
    message: String,
    first_seen_ms: u64,
    remediation: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct RuntimeSnapshot {
    machine_id: String,
    session_id: String,
    listen: SocketAddr,
    operational: String,
    safety: String,
    #[serde(default)]
    health: Vec<HealthSnapshot>,
    peers: Vec<PeerSnapshot>,
    #[serde(default = "default_direct_deployment")]
    deployment_mode: String,
    #[serde(default)]
    proxy: Option<ProxySnapshot>,
}

#[cfg(unix)]
#[derive(Debug, Deserialize)]
struct AdapterRequest {
    version: u32,
    method: String,
    #[serde(default)]
    params: serde_json::Value,
}

#[cfg(unix)]
#[derive(Debug, Deserialize)]
struct AdapterProgress {
    task_id: String,
    progress_per_mille: u16,
    stage: String,
}

#[cfg(unix)]
#[derive(Debug, Deserialize)]
struct AdapterCompletion {
    task_id: String,
    outcome: String,
    #[serde(default)]
    output: Vec<u8>,
    #[serde(default)]
    error_code: String,
    #[serde(default)]
    retryable: bool,
    #[serde(default)]
    retry_after_ms: u64,
}

#[cfg(unix)]
#[derive(Debug, Deserialize)]
struct AdapterStateInput {
    operational: String,
    safety: String,
    revision: u64,
    source_time_ms: u64,
    #[serde(default)]
    health_codes: Vec<String>,
    #[serde(default = "default_adapter_component")]
    component: String,
}

#[cfg(unix)]
fn default_adapter_component() -> String {
    "adapter".into()
}

#[cfg(unix)]
#[derive(Debug, Deserialize)]
struct AdapterReservationRevocation {
    reservation_id: String,
    previous_revision: u64,
    #[serde(default)]
    reason: String,
}

#[cfg(unix)]
#[derive(Debug, Deserialize)]
struct AdapterResourceIntrusion {
    resource_id: String,
    #[serde(default = "default_intrusion_reason")]
    reason: String,
}

#[cfg(unix)]
#[derive(Debug, Deserialize)]
struct AdapterHandoffSubjectFault {
    subject_id: String,
    #[serde(default = "default_subject_fault_reason")]
    reason: String,
}

#[cfg(unix)]
fn default_intrusion_reason() -> String {
    "zone_intrusion".into()
}

#[cfg(unix)]
fn default_subject_fault_reason() -> String {
    "physical_subject_fault".into()
}

pub async fn run_cli() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Init {
            machine_id,
            machine_class,
            dns_name,
            listen,
            protocol_minor,
            force,
        } => init_with_protocol_minor(
            &cli.data_dir,
            machine_id,
            machine_class,
            dns_name,
            listen,
            protocol_minor,
            force,
        ),
        Command::Run { once } => run_daemon(&cli.data_dir, once).await,
        Command::RotateDevelopmentCredentials { confirm_machine_id } => {
            rotate_development_credentials(&cli.data_dir, &confirm_machine_id)
        }
        Command::Peers => show_peers(&cli.data_dir),
        Command::Inspect { machine_id } => inspect(&cli.data_dir, machine_id.as_deref()),
        Command::Inspector { output } => show_inspector(&cli.data_dir, output.as_deref()),
        Command::Doctor => doctor(&cli.data_dir),
        Command::Tasks => show_tasks(&cli.data_dir),
        Command::Timeline { task_id } => show_timeline(&cli.data_dir, &task_id),
        Command::IssueTask {
            target,
            target_name,
            target_machine,
            task_id,
            capability,
            input_json,
            lease_id,
            idempotency_key,
            deadline_after_ms,
            maximum_attempts,
            idempotency,
            correlation_id,
            wait,
        } => {
            issue_task(
                &cli.data_dir,
                target,
                &target_name,
                &target_machine,
                task_id,
                capability,
                input_json,
                lease_id,
                idempotency_key,
                deadline_after_ms,
                maximum_attempts,
                idempotency,
                correlation_id,
                wait,
            )
            .await
        }
        Command::QueryTask {
            target,
            target_name,
            target_machine,
            task_id,
        } => {
            query_task(
                &cli.data_dir,
                target,
                &target_name,
                &target_machine,
                &task_id,
            )
            .await
        }
        Command::CancelTask {
            target,
            target_name,
            target_machine,
            task_id,
            reason,
        } => {
            cancel_task(
                &cli.data_dir,
                target,
                &target_name,
                &target_machine,
                &task_id,
                &reason,
            )
            .await
        }
        Command::ReserveResource {
            target,
            target_name,
            target_machine,
            reservation_id,
            task_id,
            resource_id,
            quantity,
            duration_ms,
        } => {
            reserve_resource(
                &cli.data_dir,
                target,
                &target_name,
                &target_machine,
                reservation_id,
                task_id,
                resource_id,
                quantity,
                duration_ms,
            )
            .await
        }
        Command::ReleaseResource {
            target,
            target_name,
            target_machine,
            reservation_id,
            previous_revision,
            reason,
        } => {
            release_resource(
                &cli.data_dir,
                target,
                &target_name,
                &target_machine,
                reservation_id,
                previous_revision,
                reason,
            )
            .await
        }
        Command::ProposeHandoff {
            target,
            target_name,
            target_machine,
            handoff_id,
            task_id,
            destination,
            subject_id,
            reservation_id,
            reference_frame,
            subject_frame,
            x,
            y,
            z,
            position_uncertainty_m,
            orientation_uncertainty_rad,
            maximum_age_ms,
            deadline_after_ms,
        } => {
            propose_handoff_remote(
                &cli.data_dir,
                target,
                &target_name,
                &target_machine,
                handoff_id,
                task_id,
                destination,
                subject_id,
                reservation_id,
                reference_frame,
                subject_frame,
                [x, y, z],
                position_uncertainty_m,
                orientation_uncertainty_rad,
                maximum_age_ms,
                deadline_after_ms,
            )
            .await
        }
        Command::UpdateHandoff {
            target,
            target_name,
            target_machine,
            handoff_id,
            state,
            previous_revision,
            evidence_type,
            evidence,
            reason,
        } => {
            update_handoff_remote(
                &cli.data_dir,
                target,
                &target_name,
                &target_machine,
                handoff_id,
                state,
                previous_revision,
                evidence_type,
                evidence,
                reason,
            )
            .await
        }
        Command::QueryHandoff {
            target,
            target_name,
            target_machine,
            handoff_id,
        } => {
            query_handoff_remote(
                &cli.data_dir,
                target,
                &target_name,
                &target_machine,
                &handoff_id,
            )
            .await
        }
        Command::GrantLease {
            lease_id,
            holder,
            capability,
            resource,
            duration_ms,
            clock_uncertainty_ms,
            renewable,
            exclusive,
        } => grant_lease(
            &cli.data_dir,
            lease_id,
            holder,
            capability,
            resource,
            duration_ms,
            clock_uncertainty_ms,
            renewable,
            exclusive,
        ),
        Command::RegisterCapability {
            capability,
            interruptible,
        } => register_capability(&cli.data_dir, capability, interruptible),
        Command::RegisterResource {
            resource_id,
            resource_type,
            concurrency,
            capacity,
            frame_id,
        } => register_resource(
            &cli.data_dir,
            resource_id,
            resource_type,
            concurrency,
            capacity,
            frame_id.unwrap_or_default(),
        ),
        Command::Resources => show_resources(&cli.data_dir),
        Command::Reservations => show_reservations(&cli.data_dir),
        Command::Handoffs => show_handoffs(&cli.data_dir),
        Command::CoordinationTimeline { correlation_id } => {
            show_coordination_timeline(&cli.data_dir, &correlation_id)
        }
        Command::Trust {
            machine_id,
            certificate,
            root_certificate,
            allow_metadata,
            allow_tasks,
            allow_coordination,
        } => trust(
            &cli.data_dir,
            machine_id,
            &certificate,
            &root_certificate,
            allow_metadata,
            allow_tasks,
            allow_coordination,
        ),
    }
}

pub fn init(
    data_dir: &Path,
    machine_id: String,
    machine_class: String,
    dns_name: Option<String>,
    listen: SocketAddr,
    force: bool,
) -> Result<()> {
    init_with_protocol_minor(
        data_dir,
        machine_id,
        machine_class,
        dns_name,
        listen,
        ump_runtime::PROTOCOL_MINOR,
        force,
    )
}

pub fn rotate_development_credentials(data_dir: &Path, confirm_machine_id: &str) -> Result<()> {
    let config = load_config(data_dir)?;
    if config.machine_id != confirm_machine_id {
        bail!("confirmed machine ID does not match configured identity");
    }
    if !config.development_credentials {
        bail!("credential generation is disabled for production-enrolled identities");
    }
    #[cfg(unix)]
    if std::os::unix::net::UnixStream::connect(data_dir.join(ADAPTER_SOCKET_FILE)).is_ok() {
        bail!("umpd is running; stop the runtime before rotating credentials");
    }

    let old_root = read_required(data_dir, ROOT_CERT_FILE)?;
    let old_certificate = read_required(data_dir, IDENTITY_CERT_FILE)?;
    let old_key = read_required(data_dir, IDENTITY_KEY_FILE)?;
    let old_fingerprint = certificate_fingerprint(&old_certificate);
    let archive = data_dir.join(CREDENTIAL_ARCHIVE_DIR).join(&old_fingerprint);
    if archive.exists() {
        bail!("credential archive already exists: {}", archive.display());
    }

    let pki = DevelopmentPki::generate()?;
    let identity = pki.issue(&config.dns_name)?;
    let new_root = pki.root_certificate_der().to_vec();
    let new_certificate = identity.certificate_der().to_vec();
    let new_key = identity.private_key_bytes().to_vec();
    server_config_from_der_roots(
        vec![new_root.clone()],
        new_certificate.clone(),
        new_key.clone(),
    )
    .context("generated credential set failed validation")?;

    let replacements = [
        (ROOT_CERT_FILE, new_root.as_slice(), false),
        (IDENTITY_CERT_FILE, new_certificate.as_slice(), false),
        (IDENTITY_KEY_FILE, new_key.as_slice(), true),
    ];
    for (name, contents, secret) in &replacements {
        let pending = data_dir.join(format!(".{name}.next"));
        fs::write(&pending, contents)?;
        if *secret {
            set_secret_permissions(&pending)?;
        }
    }

    fs::create_dir_all(&archive)?;
    set_directory_permissions(&archive)?;
    fs::write(archive.join(ROOT_CERT_FILE), &old_root)?;
    fs::write(archive.join(IDENTITY_CERT_FILE), &old_certificate)?;
    fs::write(archive.join(IDENTITY_KEY_FILE), &old_key)?;
    set_secret_permissions(&archive.join(IDENTITY_KEY_FILE))?;

    let activation = replacements.iter().try_for_each(|(name, _, _)| {
        fs::rename(data_dir.join(format!(".{name}.next")), data_dir.join(name))
    });
    if let Err(activation_error) = activation {
        let rollback = (|| -> Result<()> {
            fs::write(data_dir.join(ROOT_CERT_FILE), &old_root)?;
            fs::write(data_dir.join(IDENTITY_CERT_FILE), &old_certificate)?;
            fs::write(data_dir.join(IDENTITY_KEY_FILE), &old_key)?;
            set_secret_permissions(&data_dir.join(IDENTITY_KEY_FILE))?;
            Ok(())
        })();
        for (name, _, _) in &replacements {
            let _ = fs::remove_file(data_dir.join(format!(".{name}.next")));
        }
        if let Err(rollback_error) = rollback {
            bail!(
                "credential activation failed: {activation_error}; rollback failed: {rollback_error}"
            );
        }
        return Err(activation_error).context("credential activation failed; prior set restored");
    }
    let new_fingerprint = certificate_fingerprint(&new_certificate);
    println!(
        "{}",
        serde_json::json!({
            "rotated": true,
            "machine_id": config.machine_id,
            "old_certificate_fingerprint": old_fingerprint,
            "new_certificate_fingerprint": new_fingerprint,
            "archive": archive,
            "peer_reenrollment_required": true
        })
    );
    Ok(())
}

fn init_with_protocol_minor(
    data_dir: &Path,
    machine_id: String,
    machine_class: String,
    dns_name: Option<String>,
    listen: SocketAddr,
    protocol_minor: u32,
    force: bool,
) -> Result<()> {
    if !machine_id.starts_with("ump:machine:") {
        bail!("machine ID must start with ump:machine:");
    }
    if machine_class.trim().is_empty() {
        bail!("machine class cannot be empty");
    }
    if protocol_minor > ump_runtime::PROTOCOL_MINOR {
        bail!(
            "protocol minor {protocol_minor} exceeds implementation support {}",
            ump_runtime::PROTOCOL_MINOR
        );
    }
    if data_dir.exists()
        && !force
        && (!data_dir.is_dir() || fs::read_dir(data_dir)?.next().is_some())
    {
        bail!(
            "{} contains existing state; use --force to replace it",
            data_dir.display()
        );
    }
    fs::create_dir_all(data_dir)?;
    set_directory_permissions(data_dir)?;
    let dns_name = dns_name.unwrap_or_else(|| default_dns_name(&machine_id));
    let pki = DevelopmentPki::generate()?;
    let identity = pki.issue(&dns_name)?;
    let config = Config {
        machine_id,
        machine_class,
        dns_name,
        listen,
        development_credentials: true,
        protocol_minor,
        protocol_minimum_minor: 0,
        trusted_peers: BTreeMap::new(),
        task_capabilities: BTreeMap::new(),
        gateway_proxy: None,
    };
    write_json(&data_dir.join(CONFIG_FILE), &config)?;
    fs::write(data_dir.join(ROOT_CERT_FILE), pki.root_certificate_der())?;
    fs::write(
        data_dir.join(IDENTITY_CERT_FILE),
        identity.certificate_der(),
    )?;
    fs::write(
        data_dir.join(IDENTITY_KEY_FILE),
        identity.private_key_bytes(),
    )?;
    set_secret_permissions(&data_dir.join(IDENTITY_KEY_FILE))?;
    println!(
        "{}",
        serde_json::json!({
            "initialized": true,
            "machine_id": config.machine_id,
            "dns_name": config.dns_name,
            "certificate_fingerprint": identity.fingerprint(),
            "development_credentials": true
            ,"protocol_minor": config.protocol_minor
        })
    );
    Ok(())
}

pub fn doctor(data_dir: &Path) -> Result<()> {
    let config = load_config(data_dir)?;
    let root = read_required(data_dir, ROOT_CERT_FILE)?;
    let certificate = read_required(data_dir, IDENTITY_CERT_FILE)?;
    let key = read_required(data_dir, IDENTITY_KEY_FILE)?;
    let mut trust_roots = vec![root];
    trust_roots.extend(trusted_certificates(data_dir)?);
    server_config_from_der_roots(trust_roots, certificate.clone(), key)
        .context("credential chain is not usable for a mutual TLS server")?;
    let report = serde_json::json!({
        "healthy": true,
        "machine_id": config.machine_id,
        "listen": config.listen,
        "development_credentials": config.development_credentials,
        "certificate_fingerprint": certificate_fingerprint(&certificate),
        "protocol_major": ump_runtime::PROTOCOL_MAJOR,
        "protocol_minor": config.protocol_minor,
        "trusted_peer_count": config.trusted_peers.len()
    });
    println!("{report}");
    Ok(())
}

pub fn trust(
    data_dir: &Path,
    machine_id: String,
    certificate: &Path,
    root_certificate: &Path,
    allow_metadata: bool,
    allow_tasks: bool,
    allow_coordination: bool,
) -> Result<()> {
    if !machine_id.starts_with("ump:machine:") {
        bail!("peer machine ID must start with ump:machine:");
    }
    let mut config = load_config(data_dir)?;
    let certificate = fs::read(certificate)
        .with_context(|| format!("cannot read peer certificate {}", certificate.display()))?;
    let root_certificate = fs::read(root_certificate).with_context(|| {
        format!(
            "cannot read peer root certificate {}",
            root_certificate.display()
        )
    })?;
    let fingerprint = certificate_fingerprint(&certificate);
    config.trusted_peers.insert(
        fingerprint.clone(),
        TrustedPeer {
            machine_id: machine_id.clone(),
            can_read_metadata: allow_metadata,
            can_publish_metadata: allow_metadata,
            can_issue_tasks: allow_tasks,
            can_coordinate: allow_coordination,
        },
    );
    let trust_directory = data_dir.join(TRUSTED_CERTIFICATES_DIR);
    fs::create_dir_all(&trust_directory)?;
    fs::write(
        trust_directory.join(format!("{fingerprint}.der")),
        root_certificate,
    )?;
    write_json(&data_dir.join(CONFIG_FILE), &config)?;
    println!(
        "{}",
        serde_json::json!({
            "trusted": true,
            "machine_id": machine_id,
            "certificate_fingerprint": fingerprint,
            "metadata_access": allow_metadata,
            "task_issuance": allow_tasks
            ,"coordination": allow_coordination
        })
    );
    Ok(())
}

pub async fn run_daemon(data_dir: &Path, once: bool) -> Result<()> {
    let config = load_config(data_dir)?;
    let mut trust_roots = vec![read_required(data_dir, ROOT_CERT_FILE)?];
    trust_roots.extend(trusted_certificates(data_dir)?);
    let server_config = server_config_from_der_roots(
        trust_roots,
        read_required(data_dir, IDENTITY_CERT_FILE)?,
        read_required(data_dir, IDENTITY_KEY_FILE)?,
    )?;
    let endpoint = server_endpoint(config.listen, server_config)?;
    let listen = endpoint.local_addr()?;
    let session_id = format!("session-{}-{}", std::process::id(), unix_millis());
    if let Some(proxy) = &config.gateway_proxy {
        if !proxy.gateway_id.starts_with("ump:gateway:") {
            bail!("gateway proxy ID must start with ump:gateway:");
        }
        if proxy.represented_machine_id != config.machine_id {
            bail!("gateway represented machine ID must match runtime machine ID");
        }
        if proxy.controller_interface.trim().is_empty() {
            bail!("gateway controller interface cannot be empty");
        }
    }
    let read_only_proxy = config
        .gateway_proxy
        .as_ref()
        .is_some_and(|proxy| proxy.read_only);
    let descriptor = MachineDescriptor {
        machine_class: config.machine_class.clone(),
        manufacturer: "unconfigured".into(),
        model: "unconfigured".into(),
        software_version: env!("CARGO_PKG_VERSION").into(),
        endpoints: vec![Endpoint {
            uri: format!("quic://{}", listen),
            transport: TransportKind::Quic.into(),
            priority: 1,
        }],
        features: Vec::new(),
        capabilities: config
            .task_capabilities
            .iter()
            .map(|(capability, policy)| Capability {
                r#type: capability.clone(),
                version: "1.0".into(),
                label: capability.clone(),
                input_schema_uri: String::new(),
                output_schema_uri: String::new(),
                availability: CapabilityAvailability::Available.into(),
                observable: true,
                invocable: !read_only_proxy,
                reservable: false,
                interruptible: policy.interruptible,
                handoff_capable: false,
                revision: 1,
                constraints: Vec::new(),
            })
            .collect(),
        revision: 1,
        deployment_mode: if config.gateway_proxy.is_some() {
            wire::DeploymentMode::GatewayProxy.into()
        } else {
            wire::DeploymentMode::Direct.into()
        },
        proxy: config
            .gateway_proxy
            .as_ref()
            .map(|proxy| wire::ProxyAssociation {
                gateway_id: proxy.gateway_id.clone(),
                represented_machine_id: proxy.represented_machine_id.clone(),
                controller_interface: proxy.controller_interface.clone(),
                read_only: proxy.read_only,
            }),
    };
    let mut machine = Machine::with_descriptor(&config.machine_id, &session_id, descriptor)?
        .with_minimum_minor(config.protocol_minimum_minor)?
        .with_maximum_minor(config.protocol_minor)?;
    let mut task_engine = TaskEngine::open(
        &config.machine_id,
        FileJournal::new(data_dir.join(TASK_JOURNAL_FILE)),
    )?;
    for (capability, policy) in &config.task_capabilities {
        task_engine.register_capability(capability, policy.interruptible)?;
    }
    task_engine.recover_incomplete(unix_millis())?;
    let mut coordination_engine = CoordinationEngine::open(
        &config.machine_id,
        FileCoordinationJournal::new(data_dir.join(COORDINATION_JOURNAL_FILE)),
    )?;
    coordination_engine.recover_incomplete(unix_millis())?;
    coordination_engine.expire_reservations(unix_millis())?;
    coordination_engine.expire_handoffs(unix_millis())?;
    machine.publish_state(StateUpdate {
        operational: OperationalState::Idle.into(),
        safety: SafetyState::Unknown.into(),
        health: Vec::new(),
        telemetry: Vec::new(),
        revision: 1,
        source_time_ms: unix_millis(),
    })?;
    write_snapshot(data_dir, &machine, listen, &session_id)?;
    println!(
        "{}",
        serde_json::json!({
            "ready": true,
            "machine_id": config.machine_id,
            "session_id": session_id,
            "listen": listen,
            "development_credentials": config.development_credentials
        })
    );
    if once {
        return Ok(());
    }

    let adapter_socket = data_dir.join(ADAPTER_SOCKET_FILE);
    if adapter_socket.exists() {
        fs::remove_file(&adapter_socket).context("remove stale adapter socket")?;
    }
    let adapter_listener = UnixListener::bind(&adapter_socket).context("bind adapter socket")?;
    set_secret_permissions(&adapter_socket)?;
    let mut maintenance = tokio::time::interval(std::time::Duration::from_millis(100));
    maintenance.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    loop {
        let incoming = tokio::select! {
            incoming = endpoint.accept() => match incoming {
                Some(incoming) => incoming,
                None => break,
            },
            adapter = adapter_listener.accept() => {
                match adapter {
                    Ok((stream, _)) => {
                        serve_adapter_request(
                            stream,
                            &mut machine,
                            &mut task_engine,
                            &mut coordination_engine,
                        ).await;
                        write_snapshot(data_dir, &machine, listen, &session_id)?;
                    }
                    Err(error) => eprintln!("adapter connection rejected: {error}"),
                }
                continue;
            }
            _ = maintenance.tick() => {
                task_engine.expire_leases(unix_millis())?;
                coordination_engine.expire_reservations(unix_millis())?;
                coordination_engine.expire_handoffs(unix_millis())?;
                write_snapshot(data_dir, &machine, listen, &session_id)?;
                continue;
            }
        };
        let connection = match incoming.await {
            Ok(connection) => connection,
            Err(error) => {
                eprintln!("connection rejected: {error}");
                continue;
            }
        };
        let Some(fingerprint) = peer_certificate_fingerprint(&connection) else {
            connection.close(1_u8.into(), b"peer certificate missing");
            continue;
        };
        let Some(trusted_peer) = config.trusted_peers.get(&fingerprint) else {
            connection.close(2_u8.into(), b"peer is not enrolled");
            continue;
        };
        let authenticated = AuthenticatedPeer {
            machine_id: trusted_peer.machine_id.clone(),
            can_read_metadata: trusted_peer.can_read_metadata,
            can_publish_metadata: trusted_peer.can_publish_metadata,
            can_issue_tasks: trusted_peer.can_issue_tasks,
            can_coordinate: trusted_peer.can_coordinate,
        };
        let (mut send, mut receive) = match connection.accept_bi().await {
            Ok(streams) => streams,
            Err(error) => {
                eprintln!("stream rejected: {error}");
                continue;
            }
        };
        loop {
            let envelope = tokio::select! {
                envelope = read_envelope(&mut receive) => match envelope {
                    Ok(envelope) => envelope,
                    Err(_) => break,
                },
                adapter = adapter_listener.accept() => {
                    match adapter {
                        Ok((stream, _)) => {
                            serve_adapter_request(
                                stream,
                                &mut machine,
                                &mut task_engine,
                                &mut coordination_engine,
                            ).await;
                            write_snapshot(data_dir, &machine, listen, &session_id)?;
                        }
                        Err(error) => eprintln!("adapter connection rejected: {error}"),
                    }
                    continue;
                }
                _ = maintenance.tick() => {
                    task_engine.expire_leases(unix_millis())?;
                    coordination_engine.expire_reservations(unix_millis())?;
                    coordination_engine.expire_handoffs(unix_millis())?;
                    write_snapshot(data_dir, &machine, listen, &session_id)?;
                    continue;
                }
            };
            let now_ms = unix_millis();
            let task_envelope = envelope.clone();
            match machine.receive_authenticated(envelope, now_ms, &authenticated) {
                Ok(Some(response)) => {
                    let negotiated = matches!(&response.body, Some(Body::Welcome(_)));
                    if write_envelope(&mut send, &response).await.is_err() {
                        break;
                    }
                    if negotiated && authenticated.can_read_metadata {
                        let now_ms = unix_millis();
                        let advertisement = machine.advertisement(now_ms);
                        let state = machine.state_update(now_ms);
                        if write_envelope(&mut send, &advertisement).await.is_err()
                            || write_envelope(&mut send, &state).await.is_err()
                        {
                            break;
                        }
                    }
                }
                Ok(None) => {
                    if let Some(response) = process_task_message(
                        &mut machine,
                        &mut task_engine,
                        &task_envelope,
                        &authenticated,
                        now_ms,
                    )? {
                        if write_envelope(&mut send, &response).await.is_err() {
                            break;
                        }
                    } else if let Some(response) = process_coordination_message(
                        &mut machine,
                        &mut coordination_engine,
                        &task_envelope,
                        &authenticated,
                        now_ms,
                    )? {
                        if write_envelope(&mut send, &response).await.is_err() {
                            break;
                        }
                    }
                }
                Err(error) => eprintln!("protocol message rejected: {error}"),
            }
            write_snapshot(data_dir, &machine, listen, &session_id)?;
        }
    }
    Ok(())
}

#[cfg(unix)]
async fn serve_adapter_request<J: Journal, C: CoordinationJournal>(
    stream: UnixStream,
    machine: &mut Machine,
    engine: &mut TaskEngine<J>,
    coordination: &mut CoordinationEngine<C>,
) {
    let (read, mut write) = stream.into_split();
    let mut reader = AsyncBufReader::new(read);
    let mut line = String::new();
    let response = match reader.read_line(&mut line).await {
        Ok(0) => adapter_error("ump.adapter.empty_request", "request is empty"),
        Ok(_) if line.len() > MAX_ADAPTER_REQUEST_BYTES => adapter_error(
            "ump.adapter.request_too_large",
            "request exceeds size limit",
        ),
        Ok(_) => match serde_json::from_str::<AdapterRequest>(&line) {
            Ok(request) => {
                process_adapter_request(machine, engine, coordination, request, unix_millis())
                    .unwrap_or_else(|error| {
                        adapter_error("ump.adapter.rejected", &error.to_string())
                    })
            }
            Err(error) => adapter_error("ump.adapter.invalid_json", &error.to_string()),
        },
        Err(error) => adapter_error("ump.adapter.read_failed", &error.to_string()),
    };
    if let Ok(mut encoded) = serde_json::to_vec(&response) {
        encoded.push(b'\n');
        let _ = write.write_all(&encoded).await;
        let _ = write.shutdown().await;
    }
}

#[cfg(unix)]
fn process_adapter_request<J: Journal, C: CoordinationJournal>(
    machine: &mut Machine,
    engine: &mut TaskEngine<J>,
    coordination: &mut CoordinationEngine<C>,
    request: AdapterRequest,
    now_ms: u64,
) -> Result<serde_json::Value> {
    if request.version != 1 {
        bail!("unsupported local adapter API version {}", request.version);
    }
    let result = match request.method.as_str() {
        "ping" => serde_json::json!({
            "version": 1,
            "machine_id": machine.machine_id(),
            "state_revision": machine.state().revision,
            "deployment_mode": wire::DeploymentMode::try_from(machine.descriptor().deployment_mode)
                .unwrap_or(wire::DeploymentMode::Unspecified)
                .as_str_name(),
            "proxy": machine.descriptor().proxy.as_ref().map(|proxy| serde_json::json!({
                "gateway_id": proxy.gateway_id,
                "represented_machine_id": proxy.represented_machine_id,
                "controller_interface": proxy.controller_interface,
                "read_only": proxy.read_only
            }))
        }),
        "next_task" => {
            let safety =
                SafetyState::try_from(machine.state().safety).unwrap_or(SafetyState::Unknown);
            if matches!(
                safety,
                SafetyState::ProtectiveStop
                    | SafetyState::EmergencyStop
                    | SafetyState::RecoveryRequired
            ) {
                return Ok(serde_json::json!({"result": {"task": null}}));
            }
            let next_id = engine
                .tasks()
                .values()
                .find(|task| {
                    task.state == TaskState::Accepted
                        || (task.state == TaskState::RetryPending
                            && task.next_attempt_at_ms <= now_ms)
                })
                .map(|task| task.task_id.clone());
            if let Some(task_id) = next_id {
                let permit = engine.start(&task_id, now_ms)?;
                let task = engine
                    .tasks()
                    .get(&task_id)
                    .ok_or_else(|| anyhow::anyhow!("started task disappeared"))?;
                serde_json::json!({
                    "task": {
                        "task_id": task.task_id,
                        "issuer_machine_id": task.issuer_machine_id,
                        "capability": task.capability,
                        "input": task.input,
                        "input_content_type": task.input_content_type,
                        "attempt": permit.attempt,
                        "deadline_ms": task.deadline_ms,
                        "correlation_id": task.correlation_id,
                        "interruptible": task.interruptible
                    }
                })
            } else {
                serde_json::json!({"task": null})
            }
        }
        "progress" => {
            let progress: AdapterProgress = serde_json::from_value(request.params)?;
            let task = engine.progress(
                &progress.task_id,
                progress.progress_per_mille,
                &progress.stage,
                now_ms,
            )?;
            serde_json::json!({"task_id": task.task_id, "revision": task.revision})
        }
        "task_status" => {
            let task_id = request
                .params
                .get("task_id")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| anyhow::anyhow!("task_id is required"))?;
            let task = engine
                .tasks()
                .get(task_id)
                .ok_or_else(|| anyhow::anyhow!("unknown task {task_id}"))?;
            serde_json::json!({
                "task_id": task.task_id,
                "state": adapter_task_state(task.state),
                "revision": task.revision
            })
        }
        "complete" => {
            let completion: AdapterCompletion = serde_json::from_value(request.params)?;
            let task = match completion.outcome.as_str() {
                "succeeded" => engine.finish(
                    &completion.task_id,
                    TaskState::Succeeded,
                    completion.output,
                    "",
                    now_ms,
                )?,
                "failed" if completion.retryable => engine.fail_attempt(
                    &completion.task_id,
                    &completion.error_code,
                    true,
                    completion.retry_after_ms,
                    now_ms,
                )?,
                "failed" => engine.finish(
                    &completion.task_id,
                    TaskState::Failed,
                    completion.output,
                    &completion.error_code,
                    now_ms,
                )?,
                "cancelled" => engine.finish(
                    &completion.task_id,
                    TaskState::Cancelled,
                    completion.output,
                    &completion.error_code,
                    now_ms,
                )?,
                other => bail!("unsupported adapter task outcome {other}"),
            };
            serde_json::json!({
                "task_id": task.task_id,
                "state": format!("{:?}", task.state).to_lowercase(),
                "revision": task.revision
            })
        }
        "state_update" => {
            let state: AdapterStateInput = serde_json::from_value(request.params)?;
            let operational = parse_adapter_operational(&state.operational)?;
            let safety = parse_adapter_safety(&state.safety)?;
            let health = state
                .health_codes
                .into_iter()
                .map(|code| wire::HealthCondition {
                    component: state.component.clone(),
                    severity: wire::HealthSeverity::Warning.into(),
                    code,
                    message: String::new(),
                    first_seen_ms: state.source_time_ms,
                    remediation: String::new(),
                })
                .collect();
            machine.publish_state(StateUpdate {
                operational: operational.into(),
                safety: safety.into(),
                health,
                telemetry: Vec::new(),
                revision: state.revision,
                source_time_ms: state.source_time_ms,
            })?;
            serde_json::json!({"revision": state.revision})
        }
        "revoke_reservation" => {
            let revocation: AdapterReservationRevocation = serde_json::from_value(request.params)?;
            let reservation = coordination.revoke_reservation(
                &revocation.reservation_id,
                machine.machine_id(),
                revocation.previous_revision,
                &revocation.reason,
                now_ms,
            )?;
            serde_json::json!({
                "reservation_id": reservation.reservation_id,
                "state": "revoked",
                "revision": reservation.revision,
                "reason": revocation.reason
            })
        }
        "resource_intrusion" => {
            let intrusion: AdapterResourceIntrusion = serde_json::from_value(request.params)?;
            let reservation_ids: Vec<_> = coordination
                .reservations()
                .values()
                .filter(|reservation| reservation.state == NativeReservationState::Active)
                .filter(|reservation| {
                    reservation
                        .claims
                        .iter()
                        .any(|claim| claim.resource_id == intrusion.resource_id)
                })
                .map(|reservation| reservation.reservation_id.clone())
                .collect();
            let mut revoked = Vec::with_capacity(reservation_ids.len());
            for reservation_id in reservation_ids {
                let revision = coordination.reservations()[&reservation_id].revision;
                let reservation = coordination.revoke_reservation(
                    &reservation_id,
                    machine.machine_id(),
                    revision,
                    &intrusion.reason,
                    now_ms,
                )?;
                revoked.push(reservation.reservation_id);
            }
            serde_json::json!({
                "resource_id": intrusion.resource_id,
                "reason": intrusion.reason,
                "revoked_reservation_ids": revoked
            })
        }
        "handoff_subject_fault" => {
            let fault: AdapterHandoffSubjectFault = serde_json::from_value(request.params)?;
            if fault.subject_id.is_empty() || fault.subject_id.len() > 512 {
                bail!("handoff subject fault has invalid subject ID");
            }
            if fault.reason.is_empty() || fault.reason.len() > 1_024 {
                bail!("handoff subject fault has invalid reason");
            }
            let handoff_ids: Vec<_> = coordination
                .handoffs()
                .values()
                .filter(|handoff| handoff.subject_id == fault.subject_id)
                .filter(|handoff| {
                    matches!(
                        handoff.state,
                        NativeHandoffState::Proposed
                            | NativeHandoffState::Prepared
                            | NativeHandoffState::Ready
                            | NativeHandoffState::Transferring
                    )
                })
                .map(|handoff| handoff.handoff_id.clone())
                .collect();
            let mut affected = Vec::with_capacity(handoff_ids.len());
            for handoff_id in handoff_ids {
                let handoff = coordination.fail_handoff(
                    &handoff_id,
                    machine.machine_id(),
                    now_ms,
                    &fault.reason,
                )?;
                affected.push(serde_json::json!({
                    "handoff_id": handoff.handoff_id,
                    "state": format!("{:?}", handoff.state).to_lowercase(),
                    "inspection_required": handoff.inspection_required
                }));
            }
            serde_json::json!({
                "subject_id": fault.subject_id,
                "reason": fault.reason,
                "affected_handoffs": affected
            })
        }
        other => bail!("unknown local adapter method {other}"),
    };
    Ok(serde_json::json!({"result": result}))
}

#[cfg(unix)]
fn adapter_error(code: &str, detail: &str) -> serde_json::Value {
    serde_json::json!({"error": {"code": code, "detail": detail}})
}

#[cfg(unix)]
fn adapter_task_state(state: TaskState) -> &'static str {
    match state {
        TaskState::Requested => "requested",
        TaskState::Accepted => "accepted",
        TaskState::Running => "running",
        TaskState::CancelPending => "cancel_pending",
        TaskState::RetryPending => "retry_pending",
        TaskState::Succeeded => "succeeded",
        TaskState::Failed => "failed",
        TaskState::Cancelled => "cancelled",
        TaskState::Rejected => "rejected",
        TaskState::Unknown => "unknown",
    }
}

#[cfg(unix)]
fn parse_adapter_operational(value: &str) -> Result<OperationalState> {
    match value {
        "starting" => Ok(OperationalState::Starting),
        "idle" => Ok(OperationalState::Idle),
        "busy" => Ok(OperationalState::Busy),
        "paused" => Ok(OperationalState::Paused),
        "degraded" => Ok(OperationalState::Degraded),
        "stopping" => Ok(OperationalState::Stopping),
        "faulted" => Ok(OperationalState::Faulted),
        _ => bail!("invalid operational state {value}"),
    }
}

#[cfg(unix)]
fn parse_adapter_safety(value: &str) -> Result<SafetyState> {
    match value {
        "normal" => Ok(SafetyState::Normal),
        "protective_stop" => Ok(SafetyState::ProtectiveStop),
        "emergency_stop" => Ok(SafetyState::EmergencyStop),
        "recovery_required" => Ok(SafetyState::RecoveryRequired),
        "unknown" => Ok(SafetyState::Unknown),
        _ => bail!("invalid safety state {value}"),
    }
}

fn process_task_message<J: Journal>(
    machine: &mut Machine,
    engine: &mut TaskEngine<J>,
    envelope: &Envelope,
    authenticated: &AuthenticatedPeer,
    now_ms: u64,
) -> Result<Option<Envelope>> {
    let response = match envelope.body.as_ref() {
        Some(Body::TaskRequest(request)) => {
            let policy = match IdempotencyPolicy::try_from(request.idempotency_policy) {
                Ok(IdempotencyPolicy::SafeToRetry) => Some(RetryPolicy::SafeToRetry),
                Ok(IdempotencyPolicy::AtMostOnce) => Some(RetryPolicy::AtMostOnce),
                Ok(IdempotencyPolicy::ReconcileRequired) => Some(RetryPolicy::ReconcileRequired),
                _ => None,
            };
            let result = policy
                .ok_or_else(|| anyhow::anyhow!("idempotency policy is unspecified"))
                .and_then(|retry_policy| {
                    engine
                        .submit(
                            NewTask {
                                task_id: request.task_id.clone(),
                                issuer_machine_id: authenticated.machine_id.clone(),
                                capability: request.capability.clone(),
                                input: request.input.clone(),
                                input_content_type: request.input_content_type.clone(),
                                authority_lease_id: request.authority_lease_id.clone(),
                                idempotency_key: request.idempotency_key.clone(),
                                retry_policy,
                                deadline_ms: request.deadline_ms,
                                maximum_attempts: request.maximum_attempts,
                                correlation_id: envelope.correlation_id.clone(),
                                causation_id: envelope.message_id.clone(),
                            },
                            now_ms,
                        )
                        .map_err(anyhow::Error::from)
                });
            let acknowledgement = match result {
                Ok(record) => TaskAcknowledgement {
                    task_id: record.task_id,
                    status: task_status(record.state).into(),
                    reason_code: String::new(),
                    detail: String::new(),
                },
                Err(error) => TaskAcknowledgement {
                    task_id: request.task_id.clone(),
                    status: TaskStatus::Rejected.into(),
                    reason_code: "ump.task.rejected".into(),
                    detail: error.to_string(),
                },
            };
            Some(machine.protocol_message(
                now_ms,
                &envelope.correlation_id,
                &envelope.message_id,
                Body::TaskAcknowledgement(acknowledgement),
            ))
        }
        Some(Body::TaskCancel(cancel)) => {
            let result = engine.cancel(&cancel.task_id, &authenticated.machine_id, now_ms);
            let acknowledgement = match result {
                Ok(record) => TaskAcknowledgement {
                    task_id: record.task_id,
                    status: task_status(record.state).into(),
                    reason_code: String::new(),
                    detail: String::new(),
                },
                Err(error) => TaskAcknowledgement {
                    task_id: cancel.task_id.clone(),
                    status: TaskStatus::Rejected.into(),
                    reason_code: "ump.task.cancel_rejected".into(),
                    detail: error.to_string(),
                },
            };
            Some(machine.protocol_message(
                now_ms,
                &envelope.correlation_id,
                &envelope.message_id,
                Body::TaskAcknowledgement(acknowledgement),
            ))
        }
        Some(Body::TaskReconcileRequest(request)) => {
            let tasks = engine
                .tasks()
                .values()
                .filter(|task| task.issuer_machine_id == authenticated.machine_id)
                .filter(|task| {
                    request.task_ids.is_empty() || request.task_ids.contains(&task.task_id)
                })
                .map(|task| TaskSnapshot {
                    task_id: task.task_id.clone(),
                    issuer_machine_id: task.issuer_machine_id.clone(),
                    executor_machine_id: task.executor_machine_id.clone(),
                    capability: task.capability.clone(),
                    status: task_status(task.state).into(),
                    revision: task.revision,
                    idempotency_key: task.idempotency_key.clone(),
                    authority_lease_id: task.authority_lease_id.clone(),
                    inspection_required: task.inspection_required,
                })
                .collect();
            Some(machine.protocol_message(
                now_ms,
                &envelope.correlation_id,
                &envelope.message_id,
                Body::TaskReconcileResponse(TaskReconcileResponse {
                    tasks,
                    journal_sequence: engine.latest_sequence(),
                }),
            ))
        }
        _ => None,
    };
    Ok(response)
}

fn task_status(state: TaskState) -> TaskStatus {
    match state {
        TaskState::Requested => TaskStatus::Requested,
        TaskState::Accepted => TaskStatus::Accepted,
        TaskState::Running => TaskStatus::Running,
        TaskState::CancelPending => TaskStatus::CancelPending,
        TaskState::Succeeded => TaskStatus::Succeeded,
        TaskState::Failed => TaskStatus::Failed,
        TaskState::Cancelled => TaskStatus::Cancelled,
        TaskState::Rejected => TaskStatus::Rejected,
        TaskState::Unknown => TaskStatus::Unknown,
        TaskState::RetryPending => TaskStatus::RetryPending,
    }
}

fn process_coordination_message<J: CoordinationJournal>(
    machine: &mut Machine,
    engine: &mut CoordinationEngine<J>,
    envelope: &Envelope,
    authenticated: &AuthenticatedPeer,
    now_ms: u64,
) -> Result<Option<Envelope>> {
    let response_body = match envelope.body.as_ref() {
        Some(Body::ReservationRequest(request)) => {
            let result = engine.reserve(
                request.reservation_id.clone(),
                authenticated.machine_id.clone(),
                request.task_id.clone(),
                request
                    .claims
                    .iter()
                    .map(|claim| NativeClaim {
                        resource_id: claim.resource_id.clone(),
                        quantity: claim.quantity,
                    })
                    .collect(),
                request.expires_at_ms,
                now_ms,
            );
            Some(match result {
                Ok(reservation) => Body::ReservationResponse(wire::ReservationResponse {
                    reservation: Some(reservation_to_wire(&reservation)),
                    rejection_code: String::new(),
                    detail: String::new(),
                }),
                Err(error) => Body::ReservationResponse(wire::ReservationResponse {
                    reservation: None,
                    rejection_code: "ump.resource.reservation_rejected".into(),
                    detail: error.to_string(),
                }),
            })
        }
        Some(Body::ReservationRelease(release)) => {
            let result = engine.release_reservation(
                &release.reservation_id,
                &authenticated.machine_id,
                release.previous_revision,
                now_ms,
            );
            Some(match result {
                Ok(reservation) => Body::ReservationResponse(wire::ReservationResponse {
                    reservation: Some(reservation_to_wire(&reservation)),
                    rejection_code: String::new(),
                    detail: String::new(),
                }),
                Err(error) => Body::ReservationResponse(wire::ReservationResponse {
                    reservation: None,
                    rejection_code: "ump.resource.release_rejected".into(),
                    detail: error.to_string(),
                }),
            })
        }
        Some(Body::HandoffProposal(proposal)) => {
            let result =
                handoff_from_proposal(proposal, &envelope.correlation_id).and_then(|handoff| {
                    engine
                        .propose_handoff(handoff, &authenticated.machine_id, now_ms, 0.05, 0.1)
                        .map_err(anyhow::Error::from)
                });
            Some(match result {
                Ok(handoff) => Body::HandoffRecord(handoff_to_wire(&handoff)),
                Err(error) => Body::HandoffRecord(rejected_handoff_record(
                    proposal,
                    &format!("ump.handoff.proposal_rejected: {error}"),
                )),
            })
        }
        Some(Body::HandoffUpdate(update)) => {
            let result = apply_handoff_update(engine, update, &authenticated.machine_id, now_ms);
            Some(match result {
                Ok(handoff) => Body::HandoffRecord(handoff_to_wire(&handoff)),
                Err(error) => {
                    let proposal = engine
                        .handoffs()
                        .get(&update.handoff_id)
                        .map(handoff_proposal_to_wire)
                        .unwrap_or_default();
                    Body::HandoffRecord(wire::HandoffRecord {
                        proposal: Some(proposal),
                        state: wire::HandoffState::Failed.into(),
                        revision: engine
                            .handoffs()
                            .get(&update.handoff_id)
                            .map_or(0, |handoff| handoff.revision),
                        authoritative_owner_machine_id: engine
                            .handoffs()
                            .get(&update.handoff_id)
                            .map_or_else(String::new, |handoff| {
                                handoff.authoritative_owner_machine_id.clone()
                            }),
                        evidence: Vec::new(),
                        retry_safe: false,
                        inspection_required: false,
                        failure_code: format!("ump.handoff.update_rejected: {error}"),
                    })
                }
            })
        }
        Some(Body::HandoffReconcileRequest(request)) => {
            let handoffs = engine
                .handoffs()
                .values()
                .filter(|handoff| {
                    handoff.source_machine_id == authenticated.machine_id
                        || handoff.destination_machine_id == authenticated.machine_id
                })
                .filter(|handoff| {
                    request.handoff_ids.is_empty()
                        || request.handoff_ids.contains(&handoff.handoff_id)
                })
                .map(handoff_to_wire)
                .collect();
            Some(Body::HandoffReconcileResponse(
                wire::HandoffReconcileResponse {
                    handoffs,
                    journal_sequence: engine.latest_sequence(),
                },
            ))
        }
        _ => None,
    };
    Ok(response_body.map(|body| {
        machine.protocol_message(now_ms, &envelope.correlation_id, &envelope.message_id, body)
    }))
}

fn apply_handoff_update<J: CoordinationJournal>(
    engine: &mut CoordinationEngine<J>,
    update: &wire::HandoffUpdate,
    actor: &str,
    now_ms: u64,
) -> Result<NativeHandoff> {
    if update
        .evidence
        .iter()
        .any(|evidence| evidence.actor_machine_id != actor)
    {
        bail!("handoff evidence actor must match authenticated peer");
    }
    let current = engine
        .handoffs()
        .get(&update.handoff_id)
        .with_context(|| format!("handoff {} was not found", update.handoff_id))?;
    if current.revision != update.previous_revision {
        bail!("handoff revision is stale");
    }
    let target = wire::HandoffState::try_from(update.state)
        .map_err(|_| anyhow::anyhow!("handoff state is invalid"))?;
    let mut result = match target {
        wire::HandoffState::Prepared => {
            engine.prepare_handoff(&update.handoff_id, actor, now_ms)?
        }
        wire::HandoffState::Ready => engine.mark_ready(&update.handoff_id, actor, now_ms)?,
        wire::HandoffState::Transferring if current.state != NativeHandoffState::Transferring => {
            engine.begin_transfer(&update.handoff_id, actor, now_ms)?
        }
        wire::HandoffState::Transferring | wire::HandoffState::Committed => current.clone(),
        wire::HandoffState::Aborted => {
            engine.abort_handoff(&update.handoff_id, actor, now_ms, &update.reason)?
        }
        wire::HandoffState::Failed => {
            engine.fail_handoff(&update.handoff_id, actor, now_ms, &update.reason)?
        }
        _ => bail!("requested handoff state cannot be applied remotely"),
    };
    for evidence in &update.evidence {
        result = engine.add_evidence(
            &update.handoff_id,
            NativeEvidence {
                actor_machine_id: evidence.actor_machine_id.clone(),
                evidence_type: evidence.evidence_type.clone(),
                evidence: evidence.evidence.clone(),
                observed_at_ms: evidence.observed_at_ms,
            },
            now_ms,
        )?;
    }
    if target == wire::HandoffState::Committed {
        result = engine.commit_handoff(&update.handoff_id, actor, now_ms)?;
    }
    Ok(result)
}

fn handoff_from_proposal(
    proposal: &wire::HandoffProposal,
    correlation_id: &str,
) -> Result<NativeHandoff> {
    let context = proposal
        .transfer_context
        .as_ref()
        .context("transfer spatial context is missing")?;
    let pose = context.pose.as_ref().context("transfer pose is missing")?;
    let position = pose
        .position_m
        .as_ref()
        .context("transfer position is missing")?;
    let orientation = pose
        .orientation
        .as_ref()
        .context("transfer orientation is missing")?;
    Ok(NativeHandoff {
        handoff_id: proposal.handoff_id.clone(),
        task_id: proposal.task_id.clone(),
        source_machine_id: proposal.source_machine_id.clone(),
        destination_machine_id: proposal.destination_machine_id.clone(),
        subject_id: proposal.subject_id.clone(),
        reservation_id: proposal.reservation_id.clone(),
        transfer_context: NativeSpatialContext {
            reference_frame_id: context.reference_frame_id.clone(),
            subject_frame_id: context.subject_frame_id.clone(),
            position_m: [position.x, position.y, position.z],
            orientation_xyzw: [orientation.x, orientation.y, orientation.z, orientation.w],
            source_time_ms: context.source_time_ms,
            maximum_age_ms: context.maximum_age_ms,
            position_uncertainty_m: context.position_uncertainty_m,
            orientation_uncertainty_rad: context.orientation_uncertainty_rad,
        },
        preconditions: proposal.preconditions.clone(),
        deadline_ms: proposal.deadline_ms,
        state: NativeHandoffState::Proposed,
        revision: 0,
        authoritative_owner_machine_id: String::new(),
        evidence: Vec::new(),
        retry_safe: true,
        inspection_required: false,
        failure_code: String::new(),
        correlation_id: correlation_id.into(),
    })
}

fn reservation_to_wire(reservation: &NativeReservation) -> wire::ReservationRecord {
    wire::ReservationRecord {
        reservation_id: reservation.reservation_id.clone(),
        owner_machine_id: reservation.owner_machine_id.clone(),
        task_id: reservation.task_id.clone(),
        claims: reservation
            .claims
            .iter()
            .map(|claim| wire::ResourceClaim {
                resource_id: claim.resource_id.clone(),
                quantity: claim.quantity,
            })
            .collect(),
        issued_at_ms: reservation.issued_at_ms,
        expires_at_ms: reservation.expires_at_ms,
        status: match reservation.state {
            ump_runtime::coordination::ReservationState::Active => wire::ReservationStatus::Active,
            ump_runtime::coordination::ReservationState::Released => {
                wire::ReservationStatus::Released
            }
            ump_runtime::coordination::ReservationState::Expired => {
                wire::ReservationStatus::Expired
            }
            ump_runtime::coordination::ReservationState::Revoked => {
                wire::ReservationStatus::Revoked
            }
        }
        .into(),
        revision: reservation.revision,
    }
}

fn handoff_to_wire(handoff: &NativeHandoff) -> wire::HandoffRecord {
    wire::HandoffRecord {
        proposal: Some(handoff_proposal_to_wire(handoff)),
        state: native_handoff_state_to_wire(handoff.state).into(),
        revision: handoff.revision,
        authoritative_owner_machine_id: handoff.authoritative_owner_machine_id.clone(),
        evidence: handoff
            .evidence
            .iter()
            .map(|evidence| wire::HandoffEvidence {
                actor_machine_id: evidence.actor_machine_id.clone(),
                evidence_type: evidence.evidence_type.clone(),
                evidence: evidence.evidence.clone(),
                observed_at_ms: evidence.observed_at_ms,
            })
            .collect(),
        retry_safe: handoff.retry_safe,
        inspection_required: handoff.inspection_required,
        failure_code: handoff.failure_code.clone(),
    }
}

fn handoff_proposal_to_wire(handoff: &NativeHandoff) -> wire::HandoffProposal {
    wire::HandoffProposal {
        handoff_id: handoff.handoff_id.clone(),
        task_id: handoff.task_id.clone(),
        source_machine_id: handoff.source_machine_id.clone(),
        destination_machine_id: handoff.destination_machine_id.clone(),
        subject_id: handoff.subject_id.clone(),
        reservation_id: handoff.reservation_id.clone(),
        transfer_context: Some(wire::SpatialContext {
            reference_frame_id: handoff.transfer_context.reference_frame_id.clone(),
            subject_frame_id: handoff.transfer_context.subject_frame_id.clone(),
            pose: Some(wire::Pose {
                position_m: Some(wire::Vector3 {
                    x: handoff.transfer_context.position_m[0],
                    y: handoff.transfer_context.position_m[1],
                    z: handoff.transfer_context.position_m[2],
                }),
                orientation: Some(wire::Quaternion {
                    x: handoff.transfer_context.orientation_xyzw[0],
                    y: handoff.transfer_context.orientation_xyzw[1],
                    z: handoff.transfer_context.orientation_xyzw[2],
                    w: handoff.transfer_context.orientation_xyzw[3],
                }),
            }),
            source_time_ms: handoff.transfer_context.source_time_ms,
            maximum_age_ms: handoff.transfer_context.maximum_age_ms,
            position_uncertainty_m: handoff.transfer_context.position_uncertainty_m,
            orientation_uncertainty_rad: handoff.transfer_context.orientation_uncertainty_rad,
        }),
        preconditions: handoff.preconditions.clone(),
        deadline_ms: handoff.deadline_ms,
    }
}

fn rejected_handoff_record(
    proposal: &wire::HandoffProposal,
    failure_code: &str,
) -> wire::HandoffRecord {
    wire::HandoffRecord {
        proposal: Some(proposal.clone()),
        state: wire::HandoffState::Failed.into(),
        revision: 0,
        authoritative_owner_machine_id: proposal.source_machine_id.clone(),
        evidence: Vec::new(),
        retry_safe: true,
        inspection_required: false,
        failure_code: failure_code.into(),
    }
}

fn native_handoff_state_to_wire(state: NativeHandoffState) -> wire::HandoffState {
    match state {
        NativeHandoffState::Proposed => wire::HandoffState::Proposed,
        NativeHandoffState::Prepared => wire::HandoffState::Prepared,
        NativeHandoffState::Ready => wire::HandoffState::Ready,
        NativeHandoffState::Transferring => wire::HandoffState::Transferring,
        NativeHandoffState::Committed => wire::HandoffState::Committed,
        NativeHandoffState::Aborted => wire::HandoffState::Aborted,
        NativeHandoffState::Failed => wire::HandoffState::Failed,
        NativeHandoffState::Unknown => wire::HandoffState::Unknown,
    }
}

#[allow(clippy::too_many_arguments)]
async fn issue_task(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    task_id: String,
    capability: String,
    input_json: String,
    lease_id: String,
    idempotency_key: String,
    deadline_after_ms: u64,
    maximum_attempts: u32,
    idempotency: IdempotencyArg,
    correlation_id: Option<String>,
    wait: bool,
) -> Result<()> {
    let deadline_ms = unix_millis().saturating_add(deadline_after_ms);
    let mut last_error = None;
    let attempts = if wait { 4 } else { 1 };
    for attempt in 0..attempts {
        let remaining_ms = deadline_ms.saturating_sub(unix_millis());
        if remaining_ms == 0 {
            break;
        }
        match issue_task_once(
            data_dir,
            target,
            target_name,
            target_machine,
            task_id.clone(),
            capability.clone(),
            input_json.clone(),
            lease_id.clone(),
            idempotency_key.clone(),
            remaining_ms,
            maximum_attempts,
            idempotency,
            correlation_id.clone(),
            wait,
        )
        .await
        {
            Ok(()) => return Ok(()),
            Err(error) => last_error = Some(error),
        }
        if attempt + 1 < attempts {
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow::anyhow!("task deadline expired")))
}

#[allow(clippy::too_many_arguments)]
async fn issue_task_once(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    task_id: String,
    capability: String,
    input_json: String,
    lease_id: String,
    idempotency_key: String,
    deadline_after_ms: u64,
    maximum_attempts: u32,
    idempotency: IdempotencyArg,
    correlation_id: Option<String>,
    wait: bool,
) -> Result<()> {
    serde_json::from_str::<serde_json::Value>(&input_json).context("input-json is invalid")?;
    if deadline_after_ms == 0 || maximum_attempts == 0 {
        bail!("deadline-after-ms and maximum-attempts must be positive");
    }
    let config = load_config(data_dir)?;
    let roots = trusted_certificates(data_dir)?;
    if roots.is_empty() {
        bail!("no trusted peer roots; enroll the target with `ump trust` first");
    }
    let tls = client_config_from_der(
        roots,
        read_required(data_dir, IDENTITY_CERT_FILE)?,
        read_required(data_dir, IDENTITY_KEY_FILE)?,
    )?;
    let endpoint = client_endpoint(tls)?;
    let connection = connect_with_retry(&endpoint, target, target_name).await?;
    let (mut send, mut receive) = connection.open_bi().await?;
    let now_ms = unix_millis();
    let mut issuer = Machine::new(
        &config.machine_id,
        format!("cli-{}-{now_ms}", std::process::id()),
    );
    write_envelope(&mut send, &issuer.hello(now_ms)).await?;
    let welcome = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        read_envelope(&mut receive),
    )
    .await
    .context("timed out waiting for protocol welcome")??;
    if !matches!(welcome.body, Some(Body::Welcome(_)))
        || welcome.source_machine_id != target_machine
    {
        bail!("target did not return the expected authenticated welcome");
    }

    let correlation_id = correlation_id.unwrap_or_else(|| format!("corr-{task_id}"));
    let request = issuer.protocol_message(
        now_ms + 1,
        &correlation_id,
        &welcome.message_id,
        Body::TaskRequest(wire::TaskRequest {
            task_id: task_id.clone(),
            capability,
            input: input_json.into_bytes(),
            input_content_type: "application/json".into(),
            authority_lease_id: lease_id,
            idempotency_key,
            idempotency_policy: match idempotency {
                IdempotencyArg::SafeToRetry => IdempotencyPolicy::SafeToRetry,
                IdempotencyArg::AtMostOnce => IdempotencyPolicy::AtMostOnce,
                IdempotencyArg::ReconcileRequired => IdempotencyPolicy::ReconcileRequired,
            }
            .into(),
            deadline_ms: now_ms.saturating_add(deadline_after_ms),
            maximum_attempts,
        }),
    );
    write_envelope(&mut send, &request).await?;
    for _ in 0..4 {
        let response = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            read_envelope(&mut receive),
        )
        .await
        .context("timed out waiting for task acknowledgement")??;
        let response_message_id = response.message_id.clone();
        if let Some(Body::TaskAcknowledgement(acknowledgement)) = response.body {
            if acknowledgement.task_id != task_id {
                bail!("target acknowledged an unexpected task");
            }
            println!(
                "{}",
                serde_json::json!({
                    "task_id": acknowledgement.task_id,
                    "status": TaskStatus::try_from(acknowledgement.status)
                        .unwrap_or(TaskStatus::Unspecified)
                        .as_str_name()
                        .trim_start_matches("TASK_STATUS_")
                        .to_ascii_lowercase(),
                    "reason_code": acknowledgement.reason_code,
                    "detail": acknowledgement.detail,
                    "correlation_id": correlation_id,
                    "target_machine_id": target_machine
                })
            );
            if acknowledgement.status == TaskStatus::Rejected as i32 {
                connection.close(0_u8.into(), b"task command complete");
                endpoint.wait_idle().await;
                bail!("task was rejected by target");
            }
            if wait {
                let terminal = wait_task_stream(
                    &mut issuer,
                    &task_id,
                    &response_message_id,
                    deadline_after_ms,
                    &mut send,
                    &mut receive,
                )
                .await?;
                connection.close(0_u8.into(), b"task wait complete");
                endpoint.wait_idle().await;
                return if terminal == TaskStatus::Succeeded {
                    Ok(())
                } else {
                    bail!("task reached terminal status {}", terminal.as_str_name())
                };
            }
            connection.close(0_u8.into(), b"task command complete");
            endpoint.wait_idle().await;
            return Ok(());
        }
    }
    bail!("target did not acknowledge task within the response bound")
}

async fn wait_task_stream(
    machine: &mut Machine,
    task_id: &str,
    causation_id: &str,
    timeout_ms: u64,
    send: &mut quinn::SendStream,
    receive: &mut quinn::RecvStream,
) -> Result<TaskStatus> {
    let started = unix_millis();
    loop {
        if unix_millis().saturating_sub(started) >= timeout_ms {
            bail!("timed out waiting for task {task_id} to finish");
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        let now_ms = unix_millis();
        let request = machine.protocol_message(
            now_ms,
            format!("corr-wait-{task_id}"),
            causation_id,
            Body::TaskReconcileRequest(wire::TaskReconcileRequest {
                task_ids: vec![task_id.into()],
                known_journal_sequence: 0,
            }),
        );
        write_envelope(send, &request).await?;
        let response =
            tokio::time::timeout(std::time::Duration::from_secs(5), read_envelope(receive))
                .await
                .context("timed out waiting for task status")??;
        let Some(Body::TaskReconcileResponse(reconciliation)) = response.body else {
            continue;
        };
        let snapshot = reconciliation
            .tasks
            .into_iter()
            .find(|task| task.task_id == task_id)
            .with_context(|| format!("target has no task {task_id}"))?;
        let status = TaskStatus::try_from(snapshot.status).unwrap_or(TaskStatus::Unspecified);
        if matches!(
            status,
            TaskStatus::Succeeded
                | TaskStatus::Failed
                | TaskStatus::Cancelled
                | TaskStatus::Rejected
                | TaskStatus::Unknown
        ) {
            println!(
                "{}",
                serde_json::json!({
                    "task_id": snapshot.task_id,
                    "issuer_machine_id": snapshot.issuer_machine_id,
                    "executor_machine_id": snapshot.executor_machine_id,
                    "capability": snapshot.capability,
                    "status": status.as_str_name().trim_start_matches("TASK_STATUS_")
                        .to_ascii_lowercase(),
                    "revision": snapshot.revision,
                    "inspection_required": snapshot.inspection_required,
                    "journal_sequence": reconciliation.journal_sequence
                })
            );
            return Ok(status);
        }
    }
}

async fn query_task(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    task_id: &str,
) -> Result<()> {
    let config = load_config(data_dir)?;
    let roots = trusted_certificates(data_dir)?;
    if roots.is_empty() {
        bail!("no trusted peer roots; enroll the target with `ump trust` first");
    }
    let tls = client_config_from_der(
        roots,
        read_required(data_dir, IDENTITY_CERT_FILE)?,
        read_required(data_dir, IDENTITY_KEY_FILE)?,
    )?;
    let endpoint = client_endpoint(tls)?;
    let connection = connect_with_retry(&endpoint, target, target_name).await?;
    let (mut send, mut receive) = connection.open_bi().await?;
    let now_ms = unix_millis();
    let mut issuer = Machine::new(
        &config.machine_id,
        format!("cli-{}-{now_ms}", std::process::id()),
    );
    write_envelope(&mut send, &issuer.hello(now_ms)).await?;
    let welcome = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        read_envelope(&mut receive),
    )
    .await
    .context("timed out waiting for protocol welcome")??;
    if !matches!(welcome.body, Some(Body::Welcome(_)))
        || welcome.source_machine_id != target_machine
    {
        bail!("target did not return the expected authenticated welcome");
    }
    let request = issuer.protocol_message(
        now_ms + 1,
        format!("corr-query-{task_id}"),
        &welcome.message_id,
        Body::TaskReconcileRequest(wire::TaskReconcileRequest {
            task_ids: vec![task_id.into()],
            known_journal_sequence: 0,
        }),
    );
    write_envelope(&mut send, &request).await?;
    for _ in 0..4 {
        let response = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            read_envelope(&mut receive),
        )
        .await
        .context("timed out waiting for task reconciliation")??;
        if let Some(Body::TaskReconcileResponse(reconciliation)) = response.body {
            let snapshot = reconciliation
                .tasks
                .into_iter()
                .find(|task| task.task_id == task_id)
                .with_context(|| format!("target has no task {task_id}"))?;
            println!(
                "{}",
                serde_json::json!({
                    "task_id": snapshot.task_id,
                    "issuer_machine_id": snapshot.issuer_machine_id,
                    "executor_machine_id": snapshot.executor_machine_id,
                    "capability": snapshot.capability,
                    "status": TaskStatus::try_from(snapshot.status)
                        .unwrap_or(TaskStatus::Unspecified)
                        .as_str_name()
                        .trim_start_matches("TASK_STATUS_")
                        .to_ascii_lowercase(),
                    "revision": snapshot.revision,
                    "inspection_required": snapshot.inspection_required,
                    "journal_sequence": reconciliation.journal_sequence
                })
            );
            connection.close(0_u8.into(), b"task query complete");
            endpoint.wait_idle().await;
            return Ok(());
        }
    }
    bail!("target did not return task reconciliation within the response bound")
}

async fn cancel_task(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    task_id: &str,
    reason: &str,
) -> Result<()> {
    let config = load_config(data_dir)?;
    let roots = trusted_certificates(data_dir)?;
    if roots.is_empty() {
        bail!("no trusted peer roots; enroll the target with `ump trust` first");
    }
    let tls = client_config_from_der(
        roots,
        read_required(data_dir, IDENTITY_CERT_FILE)?,
        read_required(data_dir, IDENTITY_KEY_FILE)?,
    )?;
    let endpoint = client_endpoint(tls)?;
    let connection = connect_with_retry(&endpoint, target, target_name).await?;
    let (mut send, mut receive) = connection.open_bi().await?;
    let now_ms = unix_millis();
    let mut issuer = Machine::new(
        &config.machine_id,
        format!("cli-{}-{now_ms}", std::process::id()),
    );
    write_envelope(&mut send, &issuer.hello(now_ms)).await?;
    let welcome = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        read_envelope(&mut receive),
    )
    .await
    .context("timed out waiting for protocol welcome")??;
    if !matches!(welcome.body, Some(Body::Welcome(_)))
        || welcome.source_machine_id != target_machine
    {
        bail!("target did not return the expected authenticated welcome");
    }
    let request = issuer.protocol_message(
        now_ms + 1,
        format!("corr-cancel-{task_id}"),
        &welcome.message_id,
        Body::TaskCancel(wire::TaskCancel {
            task_id: task_id.into(),
            reason: reason.into(),
            requested_at_ms: now_ms,
        }),
    );
    write_envelope(&mut send, &request).await?;
    for _ in 0..4 {
        let response = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            read_envelope(&mut receive),
        )
        .await
        .context("timed out waiting for cancellation acknowledgement")??;
        if let Some(Body::TaskAcknowledgement(acknowledgement)) = response.body {
            if acknowledgement.task_id != task_id {
                bail!("target acknowledged an unexpected task");
            }
            let status =
                TaskStatus::try_from(acknowledgement.status).unwrap_or(TaskStatus::Unspecified);
            println!(
                "{}",
                serde_json::json!({
                    "task_id": acknowledgement.task_id,
                    "status": status
                        .as_str_name()
                        .trim_start_matches("TASK_STATUS_")
                        .to_ascii_lowercase(),
                    "reason_code": acknowledgement.reason_code,
                    "detail": acknowledgement.detail,
                    "target_machine_id": target_machine
                })
            );
            connection.close(0_u8.into(), b"task cancellation complete");
            endpoint.wait_idle().await;
            if status == TaskStatus::Rejected {
                bail!("task cancellation was rejected by target");
            }
            return Ok(());
        }
    }
    bail!("target did not acknowledge cancellation within the response bound")
}

async fn open_peer_session(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
) -> Result<(
    quinn::Endpoint,
    quinn::Connection,
    quinn::SendStream,
    quinn::RecvStream,
    Machine,
    Envelope,
)> {
    let config = load_config(data_dir)?;
    let roots = trusted_certificates(data_dir)?;
    if roots.is_empty() {
        bail!("no trusted peer roots; enroll the target with `ump trust` first");
    }
    let tls = client_config_from_der(
        roots,
        read_required(data_dir, IDENTITY_CERT_FILE)?,
        read_required(data_dir, IDENTITY_KEY_FILE)?,
    )?;
    let endpoint = client_endpoint(tls)?;
    let connection = connect_with_retry(&endpoint, target, target_name).await?;
    let (mut send, mut receive) = connection.open_bi().await?;
    let now_ms = unix_millis();
    let mut machine = Machine::new(
        config.machine_id,
        format!("cli-{}-{now_ms}", std::process::id()),
    );
    write_envelope(&mut send, &machine.hello(now_ms)).await?;
    let welcome = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        read_envelope(&mut receive),
    )
    .await
    .context("timed out waiting for protocol welcome")??;
    if !matches!(welcome.body, Some(Body::Welcome(_)))
        || welcome.source_machine_id != target_machine
    {
        bail!("target did not return the expected authenticated welcome");
    }
    Ok((endpoint, connection, send, receive, machine, welcome))
}

#[allow(clippy::too_many_arguments)]
async fn reserve_resource(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    reservation_id: String,
    task_id: String,
    resource_id: String,
    quantity: u32,
    duration_ms: u64,
) -> Result<()> {
    if quantity == 0 || duration_ms == 0 {
        bail!("quantity and duration-ms must be positive");
    }
    let (endpoint, connection, mut send, mut receive, mut machine, welcome) =
        open_peer_session(data_dir, target, target_name, target_machine).await?;
    let now_ms = unix_millis();
    let request = machine.protocol_message(
        now_ms,
        format!("corr-{reservation_id}"),
        welcome.message_id,
        Body::ReservationRequest(wire::ReservationRequest {
            reservation_id: reservation_id.clone(),
            task_id,
            claims: vec![wire::ResourceClaim {
                resource_id,
                quantity,
            }],
            expires_at_ms: now_ms.saturating_add(duration_ms),
        }),
    );
    write_envelope(&mut send, &request).await?;
    for _ in 0..4 {
        let response = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            read_envelope(&mut receive),
        )
        .await
        .context("timed out waiting for reservation response")??;
        if let Some(Body::ReservationResponse(response)) = response.body {
            connection.close(0_u8.into(), b"reservation command complete");
            endpoint.wait_idle().await;
            if let Some(record) = response.reservation {
                println!(
                    "{}",
                    serde_json::json!({
                        "reservation_id": record.reservation_id,
                        "owner_machine_id": record.owner_machine_id,
                        "status": wire::ReservationStatus::try_from(record.status)
                            .unwrap_or(wire::ReservationStatus::Unspecified)
                            .as_str_name().trim_start_matches("RESERVATION_STATUS_")
                            .to_ascii_lowercase(),
                        "revision": record.revision,
                        "expires_at_ms": record.expires_at_ms
                    })
                );
                return Ok(());
            }
            bail!(
                "reservation rejected: {}: {}",
                response.rejection_code,
                response.detail
            );
        }
    }
    bail!("target did not return a reservation response")
}

#[allow(clippy::too_many_arguments)]
async fn release_resource(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    reservation_id: String,
    previous_revision: u64,
    reason: String,
) -> Result<()> {
    let (endpoint, connection, mut send, mut receive, mut machine, welcome) =
        open_peer_session(data_dir, target, target_name, target_machine).await?;
    let now_ms = unix_millis();
    let request = machine.protocol_message(
        now_ms,
        format!("corr-release-{reservation_id}"),
        welcome.message_id,
        Body::ReservationRelease(wire::ReservationRelease {
            reservation_id,
            previous_revision,
            reason,
        }),
    );
    write_envelope(&mut send, &request).await?;
    for _ in 0..4 {
        let response = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            read_envelope(&mut receive),
        )
        .await
        .context("timed out waiting for reservation release")??;
        if let Some(Body::ReservationResponse(response)) = response.body {
            connection.close(0_u8.into(), b"reservation release complete");
            endpoint.wait_idle().await;
            if let Some(record) = response.reservation {
                println!(
                    "{}",
                    serde_json::json!({
                        "reservation_id": record.reservation_id,
                        "owner_machine_id": record.owner_machine_id,
                        "status": wire::ReservationStatus::try_from(record.status)
                            .unwrap_or(wire::ReservationStatus::Unspecified)
                            .as_str_name().trim_start_matches("RESERVATION_STATUS_")
                            .to_ascii_lowercase(),
                        "revision": record.revision
                    })
                );
                return Ok(());
            }
            bail!(
                "reservation release rejected: {}: {}",
                response.rejection_code,
                response.detail
            );
        }
    }
    bail!("target did not return a reservation release response")
}

#[allow(clippy::too_many_arguments)]
async fn propose_handoff_remote(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    handoff_id: String,
    task_id: String,
    destination: String,
    subject_id: String,
    reservation_id: String,
    reference_frame: String,
    subject_frame: String,
    position: [f64; 3],
    position_uncertainty_m: f64,
    orientation_uncertainty_rad: f64,
    maximum_age_ms: u64,
    deadline_after_ms: u64,
) -> Result<()> {
    let (endpoint, connection, mut send, mut receive, mut machine, welcome) =
        open_peer_session(data_dir, target, target_name, target_machine).await?;
    let now_ms = unix_millis();
    let source = machine.machine_id().to_string();
    let request = machine.protocol_message(
        now_ms,
        format!("corr-{handoff_id}"),
        welcome.message_id,
        Body::HandoffProposal(wire::HandoffProposal {
            handoff_id,
            task_id,
            source_machine_id: source,
            destination_machine_id: destination,
            subject_id,
            reservation_id,
            transfer_context: Some(wire::SpatialContext {
                reference_frame_id: reference_frame,
                subject_frame_id: subject_frame,
                pose: Some(wire::Pose {
                    position_m: Some(wire::Vector3 {
                        x: position[0],
                        y: position[1],
                        z: position[2],
                    }),
                    orientation: Some(wire::Quaternion {
                        x: 0.0,
                        y: 0.0,
                        z: 0.0,
                        w: 1.0,
                    }),
                }),
                source_time_ms: now_ms,
                maximum_age_ms,
                position_uncertainty_m,
                orientation_uncertainty_rad,
            }),
            preconditions: vec!["source_holds_subject".into(), "destination_ready".into()],
            deadline_ms: now_ms.saturating_add(deadline_after_ms),
        }),
    );
    write_envelope(&mut send, &request).await?;
    let record = read_handoff_record(&mut receive).await?;
    connection.close(0_u8.into(), b"handoff proposal complete");
    endpoint.wait_idle().await;
    print_handoff_record(&record);
    if wire::HandoffState::try_from(record.state) == Ok(wire::HandoffState::Failed) {
        bail!("handoff proposal failed: {}", record.failure_code);
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
async fn update_handoff_remote(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    handoff_id: String,
    state: HandoffStateArg,
    previous_revision: u64,
    evidence_type: Option<String>,
    evidence: Option<String>,
    reason: String,
) -> Result<()> {
    if evidence_type.is_some() != evidence.is_some() {
        bail!("evidence-type and evidence must be provided together");
    }
    let (endpoint, connection, mut send, mut receive, mut machine, welcome) =
        open_peer_session(data_dir, target, target_name, target_machine).await?;
    let now_ms = unix_millis();
    let actor = machine.machine_id().to_string();
    let evidence = evidence_type
        .into_iter()
        .zip(evidence)
        .map(|(kind, value)| wire::HandoffEvidence {
            actor_machine_id: actor.clone(),
            evidence_type: kind,
            evidence: value.into_bytes(),
            observed_at_ms: now_ms,
        })
        .collect();
    let wire_state = match state {
        HandoffStateArg::Prepared => wire::HandoffState::Prepared,
        HandoffStateArg::Ready => wire::HandoffState::Ready,
        HandoffStateArg::Transferring => wire::HandoffState::Transferring,
        HandoffStateArg::Committed => wire::HandoffState::Committed,
        HandoffStateArg::Aborted => wire::HandoffState::Aborted,
        HandoffStateArg::Failed => wire::HandoffState::Failed,
    };
    let request = machine.protocol_message(
        now_ms,
        format!("corr-{handoff_id}"),
        welcome.message_id,
        Body::HandoffUpdate(wire::HandoffUpdate {
            handoff_id,
            state: wire_state.into(),
            previous_revision,
            evidence,
            reason,
        }),
    );
    write_envelope(&mut send, &request).await?;
    let record = read_handoff_record(&mut receive).await?;
    connection.close(0_u8.into(), b"handoff update complete");
    endpoint.wait_idle().await;
    print_handoff_record(&record);
    if wire::HandoffState::try_from(record.state) == Ok(wire::HandoffState::Failed) {
        bail!("handoff update failed: {}", record.failure_code);
    }
    Ok(())
}

async fn query_handoff_remote(
    data_dir: &Path,
    target: SocketAddr,
    target_name: &str,
    target_machine: &str,
    handoff_id: &str,
) -> Result<()> {
    let (endpoint, connection, mut send, mut receive, mut machine, welcome) =
        open_peer_session(data_dir, target, target_name, target_machine).await?;
    let now_ms = unix_millis();
    let request = machine.protocol_message(
        now_ms,
        format!("corr-query-{handoff_id}"),
        welcome.message_id,
        Body::HandoffReconcileRequest(wire::HandoffReconcileRequest {
            handoff_ids: vec![handoff_id.into()],
            known_journal_sequence: 0,
        }),
    );
    write_envelope(&mut send, &request).await?;
    for _ in 0..4 {
        let response = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            read_envelope(&mut receive),
        )
        .await
        .context("timed out waiting for handoff reconciliation")??;
        if let Some(Body::HandoffReconcileResponse(response)) = response.body {
            let record = response
                .handoffs
                .into_iter()
                .find(|record| {
                    record
                        .proposal
                        .as_ref()
                        .is_some_and(|proposal| proposal.handoff_id == handoff_id)
                })
                .with_context(|| format!("target has no handoff {handoff_id}"))?;
            connection.close(0_u8.into(), b"handoff query complete");
            endpoint.wait_idle().await;
            print_handoff_record(&record);
            return Ok(());
        }
    }
    bail!("target did not return handoff reconciliation")
}

async fn read_handoff_record(receive: &mut quinn::RecvStream) -> Result<wire::HandoffRecord> {
    for _ in 0..4 {
        let response =
            tokio::time::timeout(std::time::Duration::from_secs(5), read_envelope(receive))
                .await
                .context("timed out waiting for handoff response")??;
        if let Some(Body::HandoffRecord(record)) = response.body {
            return Ok(record);
        }
    }
    bail!("target did not return a handoff record")
}

fn print_handoff_record(record: &wire::HandoffRecord) {
    let proposal = record.proposal.as_ref();
    println!(
        "{}",
        serde_json::json!({
            "handoff_id": proposal.map(|proposal| proposal.handoff_id.as_str()).unwrap_or(""),
            "state": wire::HandoffState::try_from(record.state)
                .unwrap_or(wire::HandoffState::Unspecified)
                .as_str_name().trim_start_matches("HANDOFF_STATE_").to_ascii_lowercase(),
            "revision": record.revision,
            "authoritative_owner_machine_id": record.authoritative_owner_machine_id,
            "evidence_count": record.evidence.len(),
            "evidence": record.evidence.iter().map(|evidence| serde_json::json!({
                "actor_machine_id": evidence.actor_machine_id,
                "evidence_type": evidence.evidence_type,
                "observed_at_ms": evidence.observed_at_ms,
            })).collect::<Vec<_>>(),
            "inspection_required": record.inspection_required,
            "failure_code": record.failure_code
        })
    );
}

async fn connect_with_retry(
    endpoint: &quinn::Endpoint,
    target: SocketAddr,
    target_name: &str,
) -> Result<quinn::Connection> {
    let mut last_error = "target was unavailable".to_string();
    for _ in 0..4 {
        match endpoint.connect(target, target_name) {
            Ok(connecting) => {
                match tokio::time::timeout(std::time::Duration::from_secs(4), connecting).await {
                    Ok(Ok(connection)) => return Ok(connection),
                    Ok(Err(error)) => last_error = error.to_string(),
                    Err(_) => last_error = "connection attempt timed out".into(),
                }
            }
            Err(error) => last_error = error.to_string(),
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    bail!("cannot connect to target: {last_error}")
}

fn show_peers(data_dir: &Path) -> Result<()> {
    let snapshot = load_snapshot(data_dir)?;
    println!("{}", serde_json::to_string_pretty(&snapshot.peers)?);
    Ok(())
}

fn inspect(data_dir: &Path, machine_id: Option<&str>) -> Result<()> {
    let snapshot = load_snapshot(data_dir)?;
    if let Some(machine_id) = machine_id {
        let peer = snapshot
            .peers
            .iter()
            .find(|peer| peer.machine_id == machine_id)
            .with_context(|| format!("peer {machine_id} is not present in the snapshot"))?;
        println!("{}", serde_json::to_string_pretty(peer)?);
    } else {
        println!("{}", serde_json::to_string_pretty(&snapshot)?);
    }
    Ok(())
}

fn inspector_snapshot(data_dir: &Path) -> Result<serde_json::Value> {
    let config = load_config(data_dir)?;
    let runtime = if data_dir.join(STATE_FILE).exists() {
        load_snapshot(data_dir)?
    } else {
        RuntimeSnapshot {
            machine_id: config.machine_id.clone(),
            session_id: String::new(),
            listen: config.listen,
            operational: "offline".into(),
            safety: "unknown".into(),
            health: Vec::new(),
            peers: Vec::new(),
            deployment_mode: if config.gateway_proxy.is_some() {
                "gateway_proxy".into()
            } else {
                "direct".into()
            },
            proxy: config.gateway_proxy.as_ref().map(proxy_config_snapshot),
        }
    };
    let task_engine = TaskEngine::open(
        &config.machine_id,
        FileJournal::new(data_dir.join(TASK_JOURNAL_FILE)),
    )?;
    let task_events = task_engine.journal().load()?;
    let coordination = coordination_engine(data_dir)?;
    let coordination_events = coordination.journal().load()?;

    let failed_tasks: Vec<_> = task_engine
        .tasks()
        .values()
        .filter(|task| matches!(task.state, TaskState::Failed | TaskState::Unknown))
        .map(|task| task.task_id.clone())
        .collect();
    let uncertain_handoffs: Vec<_> = coordination
        .handoffs()
        .values()
        .filter(|handoff| {
            matches!(
                handoff.state,
                NativeHandoffState::Failed | NativeHandoffState::Unknown
            )
        })
        .map(|handoff| handoff.handoff_id.clone())
        .collect();
    let expired_peers: Vec<_> = runtime
        .peers
        .iter()
        .filter(|peer| peer.status == "expired")
        .map(|peer| peer.machine_id.clone())
        .collect();
    let safety_fault = (runtime.safety != "normal").then(|| runtime.safety.clone());

    Ok(serde_json::json!({
        "generated_at_ms": unix_millis(),
        "machine": {
            "machine_id": config.machine_id,
            "machine_class": config.machine_class,
            "dns_name": config.dns_name,
            "listen": config.listen,
            "protocol_major": ump_runtime::PROTOCOL_MAJOR,
            "protocol_minor": config.protocol_minor,
            "development_credentials": config.development_credentials
        },
        "runtime": runtime,
        "trust": config.trusted_peers,
        "capabilities": config.task_capabilities,
        "tasks": task_engine.tasks(),
        "leases": task_engine.leases(),
        "resources": coordination.resources(),
        "reservations": coordination.reservations(),
        "handoffs": coordination.handoffs(),
        "events": {
            "tasks": task_events,
            "coordination": coordination_events
        },
        "faults": {
            "safety": safety_fault,
            "failed_or_unknown_task_ids": failed_tasks,
            "failed_or_unknown_handoff_ids": uncertain_handoffs,
            "expired_peer_ids": expired_peers
        }
    }))
}

fn show_inspector(data_dir: &Path, output: Option<&Path>) -> Result<()> {
    let snapshot = inspector_snapshot(data_dir)?;
    if let Some(output) = output {
        write_json(output, &snapshot)?;
    }
    println!("{}", serde_json::to_string_pretty(&snapshot)?);
    Ok(())
}

fn show_tasks(data_dir: &Path) -> Result<()> {
    let config = load_config(data_dir)?;
    let engine = TaskEngine::open(
        &config.machine_id,
        FileJournal::new(data_dir.join(TASK_JOURNAL_FILE)),
    )?;
    println!("{}", serde_json::to_string_pretty(engine.tasks())?);
    Ok(())
}

fn show_timeline(data_dir: &Path, task_id: &str) -> Result<()> {
    let journal = FileJournal::new(data_dir.join(TASK_JOURNAL_FILE));
    let events: Vec<_> = journal
        .load()?
        .into_iter()
        .filter(|event| {
            matches!(&event.payload, ump_runtime::task::JournalPayload::Task { snapshot } if snapshot.task_id == task_id)
        })
        .collect();
    if events.is_empty() {
        bail!("task {task_id} has no durable timeline");
    }
    println!("{}", serde_json::to_string_pretty(&events)?);
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn grant_lease(
    data_dir: &Path,
    lease_id: String,
    holder: String,
    capabilities: Vec<String>,
    resources: Vec<String>,
    duration_ms: u64,
    clock_uncertainty_ms: u64,
    renewable: bool,
    exclusive: bool,
) -> Result<()> {
    let config = load_config(data_dir)?;
    let now_ms = unix_millis();
    let mut engine = TaskEngine::open(
        &config.machine_id,
        FileJournal::new(data_dir.join(TASK_JOURNAL_FILE)),
    )?;
    engine.grant_lease(
        Lease {
            lease_id: lease_id.clone(),
            grantor_machine_id: config.machine_id,
            holder_machine_id: holder,
            allowed_capabilities: capabilities,
            resource_ids: resources,
            issued_at_ms: now_ms,
            expires_at_ms: now_ms.saturating_add(duration_ms),
            maximum_clock_uncertainty_ms: clock_uncertainty_ms,
            renewable,
            exclusive,
            status: LeaseStatus::Active,
            revision: 1,
        },
        now_ms,
    )?;
    println!(
        "{}",
        serde_json::json!({"granted": true, "lease_id": lease_id})
    );
    Ok(())
}

fn register_capability(data_dir: &Path, capability: String, interruptible: bool) -> Result<()> {
    if capability.trim().is_empty() {
        bail!("capability cannot be empty");
    }
    let mut config = load_config(data_dir)?;
    if config
        .task_capabilities
        .insert(capability.clone(), CapabilityConfig { interruptible })
        .is_some()
    {
        bail!("capability {capability} is already registered");
    }
    write_json(&data_dir.join(CONFIG_FILE), &config)?;
    println!(
        "{}",
        serde_json::json!({
            "registered": true,
            "capability": capability,
            "interruptible": interruptible
        })
    );
    Ok(())
}

fn coordination_engine(data_dir: &Path) -> Result<CoordinationEngine<FileCoordinationJournal>> {
    let config = load_config(data_dir)?;
    Ok(CoordinationEngine::open(
        config.machine_id,
        FileCoordinationJournal::new(data_dir.join(COORDINATION_JOURNAL_FILE)),
    )?)
}

fn register_resource(
    data_dir: &Path,
    resource_id: String,
    resource_type: String,
    concurrency: ResourceConcurrencyArg,
    capacity: u32,
    frame_id: String,
) -> Result<()> {
    let mut engine = coordination_engine(data_dir)?;
    engine.register_resource(
        NativeResource {
            resource_id: resource_id.clone(),
            resource_type,
            concurrency: match concurrency {
                ResourceConcurrencyArg::Exclusive => NativeConcurrency::Exclusive,
                ResourceConcurrencyArg::Shared => NativeConcurrency::Shared,
                ResourceConcurrencyArg::Capacity => NativeConcurrency::Capacity,
            },
            capacity,
            frame_id,
            revision: 1,
        },
        unix_millis(),
    )?;
    println!(
        "{}",
        serde_json::json!({"registered": true, "resource_id": resource_id})
    );
    Ok(())
}

fn show_resources(data_dir: &Path) -> Result<()> {
    println!(
        "{}",
        serde_json::to_string_pretty(coordination_engine(data_dir)?.resources())?
    );
    Ok(())
}

fn show_reservations(data_dir: &Path) -> Result<()> {
    println!(
        "{}",
        serde_json::to_string_pretty(coordination_engine(data_dir)?.reservations())?
    );
    Ok(())
}

fn show_handoffs(data_dir: &Path) -> Result<()> {
    println!(
        "{}",
        serde_json::to_string_pretty(coordination_engine(data_dir)?.handoffs())?
    );
    Ok(())
}

fn show_coordination_timeline(data_dir: &Path, correlation_id: &str) -> Result<()> {
    let journal = FileCoordinationJournal::new(data_dir.join(COORDINATION_JOURNAL_FILE));
    let events: Vec<_> = journal
        .load()?
        .into_iter()
        .filter(|event| event.correlation_id == correlation_id)
        .collect();
    if events.is_empty() {
        bail!("coordination correlation {correlation_id} has no durable timeline");
    }
    println!("{}", serde_json::to_string_pretty(&events)?);
    Ok(())
}

fn load_config(data_dir: &Path) -> Result<Config> {
    let bytes = read_required(data_dir, CONFIG_FILE)?;
    serde_json::from_slice(&bytes).context("config.json is invalid")
}

fn load_snapshot(data_dir: &Path) -> Result<RuntimeSnapshot> {
    let bytes = read_required(data_dir, STATE_FILE)
        .context("run `ump run` before inspecting runtime state")?;
    serde_json::from_slice(&bytes).context("runtime state is invalid")
}

fn write_snapshot(
    data_dir: &Path,
    machine: &Machine,
    listen: SocketAddr,
    session_id: &str,
) -> Result<()> {
    let peers = machine
        .peers()
        .values()
        .map(|peer| PeerSnapshot {
            machine_id: peer.machine_id.clone(),
            session_id: peer.session_id.clone(),
            status: match peer.status {
                PeerStatus::Present => "present",
                PeerStatus::Expired => "expired",
            }
            .into(),
            selected_major: peer.selected_major,
            selected_minor: peer.selected_minor,
            machine_class: peer
                .descriptor
                .as_ref()
                .map(|descriptor| descriptor.machine_class.clone()),
            capability_count: peer
                .descriptor
                .as_ref()
                .map_or(0, |descriptor| descriptor.capabilities.len()),
            state_revision: peer.state.as_ref().map(|state| state.revision),
            deployment_mode: peer
                .descriptor
                .as_ref()
                .map_or_else(default_direct_deployment, |descriptor| {
                    deployment_name(descriptor.deployment_mode)
                }),
            proxy: peer
                .descriptor
                .as_ref()
                .and_then(|descriptor| descriptor.proxy.as_ref())
                .map(proxy_wire_snapshot),
        })
        .collect();
    let snapshot = RuntimeSnapshot {
        machine_id: machine.machine_id().into(),
        session_id: session_id.into(),
        listen,
        operational: OperationalState::try_from(machine.state().operational)
            .unwrap_or(OperationalState::Unspecified)
            .as_str_name()
            .trim_start_matches("OPERATIONAL_STATE_")
            .to_ascii_lowercase(),
        safety: SafetyState::try_from(machine.state().safety)
            .unwrap_or(SafetyState::Unspecified)
            .as_str_name()
            .trim_start_matches("SAFETY_STATE_")
            .to_ascii_lowercase(),
        health: machine
            .state()
            .health
            .iter()
            .map(|condition| HealthSnapshot {
                component: condition.component.clone(),
                severity: wire::HealthSeverity::try_from(condition.severity)
                    .unwrap_or(wire::HealthSeverity::Unspecified)
                    .as_str_name()
                    .trim_start_matches("HEALTH_SEVERITY_")
                    .to_ascii_lowercase(),
                code: condition.code.clone(),
                message: condition.message.clone(),
                first_seen_ms: condition.first_seen_ms,
                remediation: condition.remediation.clone(),
            })
            .collect(),
        peers,
        deployment_mode: deployment_name(machine.descriptor().deployment_mode),
        proxy: machine.descriptor().proxy.as_ref().map(proxy_wire_snapshot),
    };
    write_json(&data_dir.join(STATE_FILE), &snapshot)
}

fn deployment_name(value: i32) -> String {
    match wire::DeploymentMode::try_from(value).unwrap_or(wire::DeploymentMode::Unspecified) {
        wire::DeploymentMode::GatewayProxy => "gateway_proxy",
        _ => "direct",
    }
    .into()
}

fn proxy_wire_snapshot(proxy: &wire::ProxyAssociation) -> ProxySnapshot {
    ProxySnapshot {
        gateway_id: proxy.gateway_id.clone(),
        represented_machine_id: proxy.represented_machine_id.clone(),
        controller_interface: proxy.controller_interface.clone(),
        read_only: proxy.read_only,
    }
}

fn proxy_config_snapshot(proxy: &GatewayProxyConfig) -> ProxySnapshot {
    ProxySnapshot {
        gateway_id: proxy.gateway_id.clone(),
        represented_machine_id: proxy.represented_machine_id.clone(),
        controller_interface: proxy.controller_interface.clone(),
        read_only: proxy.read_only,
    }
}

fn read_required(data_dir: &Path, name: &str) -> Result<Vec<u8>> {
    let path = data_dir.join(name);
    fs::read(&path).with_context(|| format!("required file is missing: {}", path.display()))
}

fn trusted_certificates(data_dir: &Path) -> Result<Vec<Vec<u8>>> {
    let directory = data_dir.join(TRUSTED_CERTIFICATES_DIR);
    if !directory.exists() {
        return Ok(Vec::new());
    }
    let mut paths = fs::read_dir(directory)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<std::io::Result<Vec<_>>>()?;
    paths.sort();
    paths
        .into_iter()
        .filter(|path| path.extension().is_some_and(|extension| extension == "der"))
        .map(|path| fs::read(&path).with_context(|| format!("cannot read {}", path.display())))
        .collect()
}

fn write_json(path: &Path, value: &impl Serialize) -> Result<()> {
    fs::write(path, serde_json::to_vec_pretty(value)?)?;
    Ok(())
}

fn default_dns_name(machine_id: &str) -> String {
    let suffix = machine_id.rsplit(':').next().unwrap_or("machine");
    format!("{suffix}.ump.local")
}

fn unix_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(unix)]
fn set_directory_permissions(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

#[cfg(not(unix))]
fn set_directory_permissions(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn set_secret_permissions(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    Ok(())
}

#[cfg(not(unix))]
fn set_secret_permissions(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ump_protocol::v1::{TaskCancel, TaskReconcileRequest, TaskRequest};
    use ump_runtime::coordination::MemoryCoordinationJournal;
    use ump_runtime::task::MemoryJournal;
    use ump_transport::{credentials::client_config_from_der, quic::client_endpoint};

    #[tokio::test]
    async fn init_doctor_and_one_shot_daemon_work() {
        let directory = tempfile::tempdir().unwrap();
        init(
            directory.path(),
            "ump:machine:test-arm".into(),
            "robot_arm".into(),
            None,
            "127.0.0.1:0".parse().unwrap(),
            false,
        )
        .unwrap();
        doctor(directory.path()).unwrap();
        run_daemon(directory.path(), true).await.unwrap();
        let snapshot = load_snapshot(directory.path()).unwrap();
        assert_eq!(snapshot.machine_id, "ump:machine:test-arm");
        assert!(snapshot.peers.is_empty());

        register_capability(
            directory.path(),
            "org.ump.manipulation.arm_pose".into(),
            true,
        )
        .unwrap();
        register_resource(
            directory.path(),
            "ump:resource:test-tool".into(),
            "org.ump.resource.tool".into(),
            ResourceConcurrencyArg::Exclusive,
            1,
            "tool0".into(),
        )
        .unwrap();
        let output = directory.path().join("inspector.json");
        show_inspector(directory.path(), Some(&output)).unwrap();
        let inspector: serde_json::Value =
            serde_json::from_slice(&fs::read(output).unwrap()).unwrap();
        assert_eq!(inspector["machine"]["machine_id"], "ump:machine:test-arm");
        assert!(inspector["capabilities"]["org.ump.manipulation.arm_pose"].is_object());
        assert!(inspector["resources"]["ump:resource:test-tool"].is_object());
        assert!(inspector["events"]["coordination"].is_array());
        assert!(inspector["faults"]["failed_or_unknown_task_ids"].is_array());
    }

    #[test]
    fn init_refuses_populated_state_without_force() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("existing"), b"preserve me").unwrap();
        let error = init(
            directory.path(),
            "ump:machine:preserve-test".into(),
            "test_fixture".into(),
            None,
            "127.0.0.1:0".parse().unwrap(),
            false,
        )
        .unwrap_err();
        assert!(error.to_string().contains("contains existing state"));
        assert_eq!(
            fs::read(directory.path().join("existing")).unwrap(),
            b"preserve me"
        );
    }

    #[test]
    fn credential_rotation_preserves_state_and_replaces_active_identity() {
        let directory = tempfile::tempdir().unwrap();
        init(
            directory.path(),
            "ump:machine:rotation-test".into(),
            "test_fixture".into(),
            Some("rotation-test.ump.local".into()),
            "127.0.0.1:0".parse().unwrap(),
            false,
        )
        .unwrap();
        register_capability(directory.path(), "org.ump.test.rotation".into(), true).unwrap();
        fs::write(directory.path().join(TASK_JOURNAL_FILE), b"audit-marker\n").unwrap();
        let original_config = fs::read(directory.path().join(CONFIG_FILE)).unwrap();
        let old_root = read_required(directory.path(), ROOT_CERT_FILE).unwrap();
        let old_certificate = read_required(directory.path(), IDENTITY_CERT_FILE).unwrap();
        let old_key = read_required(directory.path(), IDENTITY_KEY_FILE).unwrap();
        let old_fingerprint = certificate_fingerprint(&old_certificate);

        let mismatch = rotate_development_credentials(
            directory.path(),
            "ump:machine:not-the-configured-machine",
        )
        .unwrap_err();
        assert!(mismatch.to_string().contains("does not match"));
        assert_eq!(
            read_required(directory.path(), IDENTITY_CERT_FILE).unwrap(),
            old_certificate
        );

        rotate_development_credentials(directory.path(), "ump:machine:rotation-test").unwrap();
        let new_root = read_required(directory.path(), ROOT_CERT_FILE).unwrap();
        let new_certificate = read_required(directory.path(), IDENTITY_CERT_FILE).unwrap();
        let new_key = read_required(directory.path(), IDENTITY_KEY_FILE).unwrap();
        assert_ne!(new_root, old_root);
        assert_ne!(new_key, old_key);
        assert_ne!(certificate_fingerprint(&new_certificate), old_fingerprint);
        assert_eq!(
            fs::read(directory.path().join(CONFIG_FILE)).unwrap(),
            original_config
        );
        assert_eq!(
            fs::read(directory.path().join(TASK_JOURNAL_FILE)).unwrap(),
            b"audit-marker\n"
        );
        let archive = directory
            .path()
            .join(CREDENTIAL_ARCHIVE_DIR)
            .join(old_fingerprint);
        assert_eq!(fs::read(archive.join(ROOT_CERT_FILE)).unwrap(), old_root);
        assert_eq!(
            fs::read(archive.join(IDENTITY_CERT_FILE)).unwrap(),
            old_certificate
        );
        assert_eq!(fs::read(archive.join(IDENTITY_KEY_FILE)).unwrap(), old_key);
        doctor(directory.path()).unwrap();
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn credential_rotation_refuses_a_running_daemon() {
        let directory = tempfile::tempdir().unwrap();
        init(
            directory.path(),
            "ump:machine:rotation-running".into(),
            "test_fixture".into(),
            None,
            "127.0.0.1:0".parse().unwrap(),
            false,
        )
        .unwrap();
        let data_dir = directory.path().to_path_buf();
        let daemon = tokio::spawn(async move { run_daemon(&data_dir, false).await });
        let adapter_socket = directory.path().join(ADAPTER_SOCKET_FILE);
        for _ in 0..50 {
            if adapter_socket.exists() {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        let error =
            rotate_development_credentials(directory.path(), "ump:machine:rotation-running")
                .unwrap_err();
        assert!(error.to_string().contains("umpd is running"));
        daemon.abort();
        let _ = daemon.await;
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn daemon_serves_the_bounded_local_adapter_socket() {
        let directory = tempfile::tempdir().unwrap();
        init(
            directory.path(),
            "ump:machine:socket-test".into(),
            "mobile_base".into(),
            None,
            "127.0.0.1:0".parse().unwrap(),
            true,
        )
        .unwrap();
        let data_dir = directory.path().to_path_buf();
        let daemon = tokio::spawn(async move { run_daemon(&data_dir, false).await });
        let socket_path = directory.path().join(ADAPTER_SOCKET_FILE);
        for _ in 0..100 {
            if socket_path.exists() {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        let mut stream = UnixStream::connect(&socket_path).await.unwrap();
        stream
            .write_all(b"{\"version\":1,\"method\":\"ping\",\"params\":{}}\n")
            .await
            .unwrap();
        let mut response = String::new();
        AsyncBufReader::new(stream)
            .read_line(&mut response)
            .await
            .unwrap();
        let response: serde_json::Value = serde_json::from_str(&response).unwrap();
        assert_eq!(response["result"]["machine_id"], "ump:machine:socket-test");
        daemon.abort();
    }

    #[test]
    fn trust_binds_fingerprint_and_denies_metadata_by_default() {
        let directory = tempfile::tempdir().unwrap();
        init(
            directory.path(),
            "ump:machine:test-arm".into(),
            "robot_arm".into(),
            None,
            "127.0.0.1:0".parse().unwrap(),
            true,
        )
        .unwrap();
        let peer_pki = DevelopmentPki::generate().unwrap();
        let peer = peer_pki.issue("peer.ump.local").unwrap();
        let peer_path = directory.path().join("peer.der");
        let peer_root_path = directory.path().join("peer-root.der");
        fs::write(&peer_path, peer.certificate_der()).unwrap();
        fs::write(&peer_root_path, peer_pki.root_certificate_der()).unwrap();
        trust(
            directory.path(),
            "ump:machine:peer".into(),
            &peer_path,
            &peer_root_path,
            false,
            false,
            false,
        )
        .unwrap();
        doctor(directory.path()).unwrap();
        let config = load_config(directory.path()).unwrap();
        assert_eq!(
            config
                .trusted_peers
                .get(&peer.fingerprint())
                .map(|peer| peer.machine_id.as_str()),
            Some("ump:machine:peer")
        );
        let trusted = config.trusted_peers.get(&peer.fingerprint()).unwrap();
        assert!(!trusted.can_read_metadata);
        assert!(!trusted.can_publish_metadata);
        assert!(!trusted.can_issue_tasks);
        assert!(!trusted.can_coordinate);
    }

    #[test]
    fn grants_authority_and_exposes_durable_task_views() {
        let directory = tempfile::tempdir().unwrap();
        init(
            directory.path(),
            "ump:machine:test-base".into(),
            "mobile_base".into(),
            None,
            "127.0.0.1:0".parse().unwrap(),
            true,
        )
        .unwrap();
        grant_lease(
            directory.path(),
            "lease-cli".into(),
            "ump:machine:coordinator".into(),
            vec!["org.ump.logistics.deliver".into()],
            Vec::new(),
            60_000,
            10,
            true,
            true,
        )
        .unwrap();
        let config = load_config(directory.path()).unwrap();
        let engine = TaskEngine::open(
            config.machine_id,
            FileJournal::new(directory.path().join(TASK_JOURNAL_FILE)),
        )
        .unwrap();
        assert_eq!(engine.leases()["lease-cli"].status, LeaseStatus::Active);
        show_tasks(directory.path()).unwrap();
        register_resource(
            directory.path(),
            "ump:resource:test-zone".into(),
            "org.ump.resource.transfer_zone".into(),
            ResourceConcurrencyArg::Exclusive,
            1,
            "ump:frame:test-zone".into(),
        )
        .unwrap();
        show_resources(directory.path()).unwrap();
    }

    #[test]
    fn authenticated_task_request_is_persisted_and_acknowledged_once() {
        let executor_id = "ump:machine:executor";
        let issuer_id = "ump:machine:issuer";
        let mut engine = TaskEngine::open(executor_id, MemoryJournal::default()).unwrap();
        engine
            .register_capability("org.ump.logistics.deliver", false)
            .unwrap();
        engine
            .grant_lease(
                Lease {
                    lease_id: "lease-network".into(),
                    grantor_machine_id: executor_id.into(),
                    holder_machine_id: issuer_id.into(),
                    allowed_capabilities: vec!["org.ump.logistics.deliver".into()],
                    resource_ids: Vec::new(),
                    issued_at_ms: 0,
                    expires_at_ms: 10_000,
                    maximum_clock_uncertainty_ms: 0,
                    renewable: true,
                    exclusive: true,
                    status: LeaseStatus::Active,
                    revision: 1,
                },
                1,
            )
            .unwrap();
        let mut issuer = Machine::new(issuer_id, "issuer-session");
        let mut executor = Machine::new(executor_id, "executor-session");
        let authenticated = AuthenticatedPeer {
            machine_id: issuer_id.into(),
            can_read_metadata: false,
            can_publish_metadata: false,
            can_issue_tasks: true,
            can_coordinate: false,
        };
        let body = Body::TaskRequest(TaskRequest {
            task_id: "task-network".into(),
            capability: "org.ump.logistics.deliver".into(),
            input: b"package-1".to_vec(),
            input_content_type: "application/octet-stream".into(),
            authority_lease_id: "lease-network".into(),
            idempotency_key: "idem-network".into(),
            idempotency_policy: IdempotencyPolicy::AtMostOnce.into(),
            deadline_ms: 9_000,
            maximum_attempts: 1,
        });
        let request = issuer.protocol_message(2, "corr-network", "", body.clone());
        executor
            .receive_authenticated(request.clone(), 2, &authenticated)
            .unwrap();
        let response =
            process_task_message(&mut executor, &mut engine, &request, &authenticated, 2)
                .unwrap()
                .unwrap();
        assert!(matches!(
            response.body,
            Some(Body::TaskAcknowledgement(TaskAcknowledgement {
                status,
                ..
            })) if status == TaskStatus::Accepted as i32
        ));
        assert_eq!(engine.tasks()["task-network"].attempts_started, 0);

        let duplicate = issuer.protocol_message(3, "corr-network", "", body);
        executor
            .receive_authenticated(duplicate.clone(), 3, &authenticated)
            .unwrap();
        process_task_message(&mut executor, &mut engine, &duplicate, &authenticated, 3).unwrap();
        assert_eq!(engine.tasks().len(), 1);
        assert_eq!(engine.tasks()["task-network"].attempts_started, 0);
    }

    #[cfg(unix)]
    #[test]
    fn local_adapter_claims_reports_and_completes_authorized_work() {
        let executor_id = "ump:machine:adapter-executor";
        let issuer_id = "ump:machine:adapter-issuer";
        let capability = "org.ump.navigation.navigate";
        let mut machine = Machine::new(executor_id, "adapter-session");
        let mut engine = TaskEngine::open(executor_id, MemoryJournal::default()).unwrap();
        let mut coordination =
            CoordinationEngine::open(executor_id, MemoryCoordinationJournal::default()).unwrap();
        engine.register_capability(capability, true).unwrap();
        engine
            .grant_lease(
                Lease {
                    lease_id: "lease-adapter".into(),
                    grantor_machine_id: executor_id.into(),
                    holder_machine_id: issuer_id.into(),
                    allowed_capabilities: vec![capability.into()],
                    resource_ids: Vec::new(),
                    issued_at_ms: 1,
                    expires_at_ms: 10_000,
                    maximum_clock_uncertainty_ms: 0,
                    renewable: true,
                    exclusive: true,
                    status: LeaseStatus::Active,
                    revision: 1,
                },
                1,
            )
            .unwrap();
        engine
            .submit(
                NewTask {
                    task_id: "task-adapter".into(),
                    issuer_machine_id: issuer_id.into(),
                    capability: capability.into(),
                    input: b"{\"x\":1.0}".to_vec(),
                    input_content_type: "application/json".into(),
                    authority_lease_id: "lease-adapter".into(),
                    idempotency_key: "idem-adapter".into(),
                    retry_policy: RetryPolicy::AtMostOnce,
                    deadline_ms: 9_000,
                    maximum_attempts: 1,
                    correlation_id: "corr-adapter".into(),
                    causation_id: "message-adapter".into(),
                },
                2,
            )
            .unwrap();

        let claimed = process_adapter_request(
            &mut machine,
            &mut engine,
            &mut coordination,
            AdapterRequest {
                version: 1,
                method: "next_task".into(),
                params: serde_json::json!({}),
            },
            3,
        )
        .unwrap();
        assert_eq!(claimed["result"]["task"]["task_id"], "task-adapter");
        assert_eq!(engine.tasks()["task-adapter"].state, TaskState::Running);

        process_adapter_request(
            &mut machine,
            &mut engine,
            &mut coordination,
            AdapterRequest {
                version: 1,
                method: "progress".into(),
                params: serde_json::json!({
                    "task_id": "task-adapter",
                    "progress_per_mille": 500,
                    "stage": "approach"
                }),
            },
            4,
        )
        .unwrap();
        let status = process_adapter_request(
            &mut machine,
            &mut engine,
            &mut coordination,
            AdapterRequest {
                version: 1,
                method: "task_status".into(),
                params: serde_json::json!({"task_id": "task-adapter"}),
            },
            4,
        )
        .unwrap();
        assert_eq!(status["result"]["state"], "running");
        process_adapter_request(
            &mut machine,
            &mut engine,
            &mut coordination,
            AdapterRequest {
                version: 1,
                method: "complete".into(),
                params: serde_json::json!({
                    "task_id": "task-adapter",
                    "outcome": "succeeded",
                    "output": [111, 107]
                }),
            },
            5,
        )
        .unwrap();
        assert_eq!(engine.tasks()["task-adapter"].state, TaskState::Succeeded);
        assert_eq!(engine.tasks()["task-adapter"].result, b"ok");

        process_adapter_request(
            &mut machine,
            &mut engine,
            &mut coordination,
            AdapterRequest {
                version: 1,
                method: "state_update".into(),
                params: serde_json::json!({
                    "operational": "idle",
                    "safety": "normal",
                    "revision": 1,
                    "source_time_ms": 6
                }),
            },
            6,
        )
        .unwrap();
        assert_eq!(machine.state().safety, SafetyState::Normal as i32);
    }

    #[cfg(unix)]
    #[test]
    fn zone_adapter_revokes_its_own_active_reservation() {
        let authority = "ump:machine:zone-authority";
        let mut machine = Machine::new(authority, "zone-session");
        let mut tasks = TaskEngine::open(authority, MemoryJournal::default()).unwrap();
        let mut coordination =
            CoordinationEngine::open(authority, MemoryCoordinationJournal::default()).unwrap();
        coordination
            .register_resource(
                NativeResource {
                    resource_id: "ump:resource:zone".into(),
                    resource_type: "org.ump.resource.transfer_zone".into(),
                    concurrency: NativeConcurrency::Exclusive,
                    capacity: 1,
                    frame_id: "world".into(),
                    revision: 1,
                },
                1,
            )
            .unwrap();
        coordination
            .reserve(
                "ump:reservation:intrusion",
                "ump:machine:arm",
                "ump:task:handoff",
                vec![NativeClaim {
                    resource_id: "ump:resource:zone".into(),
                    quantity: 1,
                }],
                10_000,
                2,
            )
            .unwrap();
        let response = process_adapter_request(
            &mut machine,
            &mut tasks,
            &mut coordination,
            AdapterRequest {
                version: 1,
                method: "resource_intrusion".into(),
                params: serde_json::json!({
                    "resource_id": "ump:resource:zone",
                    "reason": "zone_intrusion"
                }),
            },
            3,
        )
        .unwrap();
        assert_eq!(response["result"]["reason"], "zone_intrusion");
        assert_eq!(
            response["result"]["revoked_reservation_ids"],
            serde_json::json!(["ump:reservation:intrusion"])
        );
        assert!(
            coordination
                .journal()
                .events()
                .iter()
                .any(|event| event.reason.contains("zone_intrusion"))
        );
    }

    #[cfg(unix)]
    #[test]
    fn payload_monitor_marks_a_transferring_subject_unknown() {
        let authority = "ump:machine:zone-authority";
        let source = "ump:machine:arm";
        let destination = "ump:machine:mobile";
        let mut machine = Machine::new(authority, "zone-session");
        let mut tasks = TaskEngine::open(authority, MemoryJournal::default()).unwrap();
        let mut coordination =
            CoordinationEngine::open(authority, MemoryCoordinationJournal::default()).unwrap();
        coordination
            .register_resource(
                NativeResource {
                    resource_id: "ump:resource:zone".into(),
                    resource_type: "org.ump.resource.transfer_zone".into(),
                    concurrency: NativeConcurrency::Exclusive,
                    capacity: 1,
                    frame_id: "world".into(),
                    revision: 1,
                },
                1,
            )
            .unwrap();
        coordination
            .reserve(
                "ump:reservation:drop",
                source,
                "ump:task:drop",
                vec![NativeClaim {
                    resource_id: "ump:resource:zone".into(),
                    quantity: 1,
                }],
                10_000,
                2,
            )
            .unwrap();
        coordination
            .propose_handoff(
                NativeHandoff {
                    handoff_id: "ump:handoff:drop".into(),
                    task_id: "ump:task:drop".into(),
                    source_machine_id: source.into(),
                    destination_machine_id: destination.into(),
                    subject_id: "ump:subject:package".into(),
                    reservation_id: "ump:reservation:drop".into(),
                    transfer_context: NativeSpatialContext {
                        reference_frame_id: "world".into(),
                        subject_frame_id: "package/body".into(),
                        position_m: [0.0, 0.0, 0.5],
                        orientation_xyzw: [0.0, 0.0, 0.0, 1.0],
                        source_time_ms: 2,
                        maximum_age_ms: 1_000,
                        position_uncertainty_m: 0.01,
                        orientation_uncertainty_rad: 0.01,
                    },
                    preconditions: Vec::new(),
                    deadline_ms: 10_000,
                    state: NativeHandoffState::Proposed,
                    revision: 0,
                    authoritative_owner_machine_id: String::new(),
                    evidence: Vec::new(),
                    retry_safe: true,
                    inspection_required: false,
                    failure_code: String::new(),
                    correlation_id: "corr-drop".into(),
                },
                source,
                2,
                0.1,
                0.1,
            )
            .unwrap();
        coordination
            .prepare_handoff("ump:handoff:drop", source, 3)
            .unwrap();
        coordination
            .mark_ready("ump:handoff:drop", destination, 4)
            .unwrap();
        coordination
            .begin_transfer("ump:handoff:drop", source, 5)
            .unwrap();

        let response = process_adapter_request(
            &mut machine,
            &mut tasks,
            &mut coordination,
            AdapterRequest {
                version: 1,
                method: "handoff_subject_fault".into(),
                params: serde_json::json!({
                    "subject_id": "ump:subject:package",
                    "reason": "payload_dropped"
                }),
            },
            6,
        )
        .unwrap();
        assert_eq!(
            response["result"]["affected_handoffs"][0]["state"],
            "unknown"
        );
        let handoff = &coordination.handoffs()["ump:handoff:drop"];
        assert!(handoff.inspection_required);
        assert_eq!(handoff.authoritative_owner_machine_id, source);
        assert_eq!(handoff.failure_code, "ump.handoff.outcome_unknown");

        let rejected = process_adapter_request(
            &mut machine,
            &mut tasks,
            &mut coordination,
            AdapterRequest {
                version: 1,
                method: "handoff_subject_fault".into(),
                params: serde_json::json!({"subject_id": "", "reason": "drop"}),
            },
            7,
        );
        assert!(rejected.is_err());
    }

    #[tokio::test]
    async fn daemon_accepts_authorized_task_over_mutual_quic() {
        let directory = tempfile::tempdir().unwrap();
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        let listen = socket.local_addr().unwrap();
        drop(socket);
        init(
            directory.path(),
            "ump:machine:network-executor".into(),
            "mobile_base".into(),
            Some("network-executor.ump.local".into()),
            listen,
            true,
        )
        .unwrap();

        let issuer_pki = DevelopmentPki::generate().unwrap();
        let issuer_identity = issuer_pki.issue("network-issuer.ump.local").unwrap();
        let issuer_cert_path = directory.path().join("issuer.der");
        let issuer_root_path = directory.path().join("issuer-root.der");
        fs::write(&issuer_cert_path, issuer_identity.certificate_der()).unwrap();
        fs::write(&issuer_root_path, issuer_pki.root_certificate_der()).unwrap();
        trust(
            directory.path(),
            "ump:machine:network-issuer".into(),
            &issuer_cert_path,
            &issuer_root_path,
            false,
            true,
            true,
        )
        .unwrap();
        register_capability(directory.path(), "org.ump.logistics.deliver".into(), false).unwrap();
        register_resource(
            directory.path(),
            "ump:resource:network-zone".into(),
            "org.ump.resource.transfer_zone".into(),
            ResourceConcurrencyArg::Exclusive,
            1,
            "ump:frame:network-zone".into(),
        )
        .unwrap();
        grant_lease(
            directory.path(),
            "lease-quic".into(),
            "ump:machine:network-issuer".into(),
            vec!["org.ump.logistics.deliver".into()],
            Vec::new(),
            60_000,
            10,
            true,
            true,
        )
        .unwrap();

        let server_root = read_required(directory.path(), ROOT_CERT_FILE).unwrap();
        let client_config = client_config_from_der(
            vec![server_root],
            issuer_identity.certificate_der().to_vec(),
            issuer_identity.private_key_bytes().to_vec(),
        )
        .unwrap();
        let client = client_endpoint(client_config).unwrap();
        let data_dir = directory.path().to_path_buf();
        let daemon = tokio::spawn(async move { run_daemon(&data_dir, false).await });

        let connection = {
            let mut connected = None;
            for _ in 0..50 {
                match client.connect(listen, "network-executor.ump.local") {
                    Ok(connecting) => match connecting.await {
                        Ok(connection) => {
                            connected = Some(connection);
                            break;
                        }
                        Err(_) => tokio::time::sleep(std::time::Duration::from_millis(10)).await,
                    },
                    Err(_) => tokio::time::sleep(std::time::Duration::from_millis(10)).await,
                }
            }
            connected.expect("daemon did not start")
        };
        let (mut send, mut receive) = connection.open_bi().await.unwrap();
        let mut issuer = Machine::new("ump:machine:network-issuer", "issuer-session");
        write_envelope(&mut send, &issuer.hello(unix_millis()))
            .await
            .unwrap();
        let welcome = read_envelope(&mut receive).await.unwrap();
        assert!(matches!(welcome.body, Some(Body::Welcome(_))));

        let now_ms = unix_millis();
        let request = issuer.protocol_message(
            now_ms,
            "corr-quic",
            "",
            Body::TaskRequest(TaskRequest {
                task_id: "task-quic".into(),
                capability: "org.ump.logistics.deliver".into(),
                input: b"package-quic".to_vec(),
                input_content_type: "application/octet-stream".into(),
                authority_lease_id: "lease-quic".into(),
                idempotency_key: "idem-quic".into(),
                idempotency_policy: IdempotencyPolicy::AtMostOnce.into(),
                deadline_ms: now_ms + 30_000,
                maximum_attempts: 1,
            }),
        );
        write_envelope(&mut send, &request).await.unwrap();
        let acknowledgement = read_envelope(&mut receive).await.unwrap();
        assert!(matches!(
            acknowledgement.body,
            Some(Body::TaskAcknowledgement(TaskAcknowledgement { status, .. }))
                if status == TaskStatus::Accepted as i32
        ));

        let reconcile = issuer.protocol_message(
            now_ms + 1,
            "corr-reconcile",
            acknowledgement.message_id,
            Body::TaskReconcileRequest(TaskReconcileRequest {
                task_ids: vec!["task-quic".into()],
                known_journal_sequence: 0,
            }),
        );
        write_envelope(&mut send, &reconcile).await.unwrap();
        let reconciliation = read_envelope(&mut receive).await.unwrap();
        let Some(Body::TaskReconcileResponse(reconciliation)) = reconciliation.body else {
            panic!("expected reconciliation response")
        };
        assert_eq!(reconciliation.tasks.len(), 1);
        assert_eq!(reconciliation.tasks[0].task_id, "task-quic");
        assert_eq!(reconciliation.tasks[0].status, TaskStatus::Accepted as i32);

        let cancel = issuer.protocol_message(
            now_ms + 2,
            "corr-quic",
            reconcile.message_id,
            Body::TaskCancel(TaskCancel {
                task_id: "task-quic".into(),
                reason: "operator request".into(),
                requested_at_ms: now_ms + 2,
            }),
        );
        write_envelope(&mut send, &cancel).await.unwrap();
        let cancelled = read_envelope(&mut receive).await.unwrap();
        assert!(matches!(
            cancelled.body,
            Some(Body::TaskAcknowledgement(TaskAcknowledgement { status, .. }))
                if status == TaskStatus::Cancelled as i32
        ));

        let reservation = issuer.protocol_message(
            now_ms + 3,
            "corr-network-reservation",
            cancel.message_id,
            Body::ReservationRequest(wire::ReservationRequest {
                reservation_id: "ump:reservation:network-zone".into(),
                task_id: "task-quic".into(),
                claims: vec![wire::ResourceClaim {
                    resource_id: "ump:resource:network-zone".into(),
                    quantity: 1,
                }],
                expires_at_ms: now_ms + 30_000,
            }),
        );
        write_envelope(&mut send, &reservation).await.unwrap();
        let reservation_response = read_envelope(&mut receive).await.unwrap();
        assert!(matches!(
            reservation_response.body,
            Some(Body::ReservationResponse(wire::ReservationResponse {
                reservation: Some(_),
                ..
            }))
        ));

        let proposal = issuer.protocol_message(
            now_ms + 4,
            "corr-network-handoff",
            reservation.message_id,
            Body::HandoffProposal(wire::HandoffProposal {
                handoff_id: "ump:handoff:network-zone".into(),
                task_id: "task-quic".into(),
                source_machine_id: "ump:machine:network-issuer".into(),
                destination_machine_id: "ump:machine:network-executor".into(),
                subject_id: "ump:subject:network-package".into(),
                reservation_id: "ump:reservation:network-zone".into(),
                transfer_context: Some(wire::SpatialContext {
                    reference_frame_id: "ump:frame:network-zone".into(),
                    subject_frame_id: "ump:frame:network-package".into(),
                    pose: Some(wire::Pose {
                        position_m: Some(wire::Vector3 {
                            x: 0.5,
                            y: 0.0,
                            z: 0.8,
                        }),
                        orientation: Some(wire::Quaternion {
                            x: 0.0,
                            y: 0.0,
                            z: 0.0,
                            w: 1.0,
                        }),
                    }),
                    source_time_ms: now_ms + 4,
                    maximum_age_ms: 1_000,
                    position_uncertainty_m: 0.005,
                    orientation_uncertainty_rad: 0.01,
                }),
                preconditions: vec!["destination_ready".into()],
                deadline_ms: now_ms + 20_000,
            }),
        );
        write_envelope(&mut send, &proposal).await.unwrap();
        let handoff_response = read_envelope(&mut receive).await.unwrap();
        assert!(matches!(
            handoff_response.body,
            Some(Body::HandoffRecord(wire::HandoffRecord { state, .. }))
                if state == wire::HandoffState::Proposed as i32
        ));
        daemon.abort();
        let _ = daemon.await;

        let engine = TaskEngine::open(
            "ump:machine:network-executor",
            FileJournal::new(directory.path().join(TASK_JOURNAL_FILE)),
        )
        .unwrap();
        assert_eq!(engine.tasks()["task-quic"].state, TaskState::Cancelled);
        let coordination = coordination_engine(directory.path()).unwrap();
        assert_eq!(
            coordination.handoffs()["ump:handoff:network-zone"].authoritative_owner_machine_id,
            "ump:machine:network-issuer"
        );
    }

    #[test]
    fn authenticated_coordination_reserves_zone_and_persists_proposal() {
        let source = "ump:machine:source";
        let destination = "ump:machine:destination";
        let authority = "ump:machine:coordinator";
        let mut engine =
            CoordinationEngine::open(authority, MemoryCoordinationJournal::default()).unwrap();
        engine
            .register_resource(
                NativeResource {
                    resource_id: "ump:resource:zone".into(),
                    resource_type: "org.ump.resource.transfer_zone".into(),
                    concurrency: NativeConcurrency::Exclusive,
                    capacity: 1,
                    frame_id: "ump:frame:zone".into(),
                    revision: 1,
                },
                0,
            )
            .unwrap();
        let authenticated = AuthenticatedPeer {
            machine_id: source.into(),
            can_read_metadata: false,
            can_publish_metadata: false,
            can_issue_tasks: false,
            can_coordinate: true,
        };
        let mut source_machine = Machine::new(source, "source-session");
        let mut authority_machine = Machine::new(authority, "authority-session");
        let reservation = source_machine.protocol_message(
            1,
            "corr-reservation",
            "",
            Body::ReservationRequest(wire::ReservationRequest {
                reservation_id: "ump:reservation:zone".into(),
                task_id: "ump:task:handoff".into(),
                claims: vec![wire::ResourceClaim {
                    resource_id: "ump:resource:zone".into(),
                    quantity: 1,
                }],
                expires_at_ms: 10_000,
            }),
        );
        authority_machine
            .receive_authenticated(reservation.clone(), 1, &authenticated)
            .unwrap();
        let response = process_coordination_message(
            &mut authority_machine,
            &mut engine,
            &reservation,
            &authenticated,
            1,
        )
        .unwrap()
        .unwrap();
        assert!(matches!(
            response.body,
            Some(Body::ReservationResponse(wire::ReservationResponse {
                reservation: Some(_),
                ..
            }))
        ));

        let proposal = source_machine.protocol_message(
            2,
            "corr-handoff",
            reservation.message_id,
            Body::HandoffProposal(wire::HandoffProposal {
                handoff_id: "ump:handoff:zone".into(),
                task_id: "ump:task:handoff".into(),
                source_machine_id: source.into(),
                destination_machine_id: destination.into(),
                subject_id: "ump:subject:package".into(),
                reservation_id: "ump:reservation:zone".into(),
                transfer_context: Some(wire::SpatialContext {
                    reference_frame_id: "ump:frame:zone".into(),
                    subject_frame_id: "ump:frame:package".into(),
                    pose: Some(wire::Pose {
                        position_m: Some(wire::Vector3 {
                            x: 1.0,
                            y: 0.0,
                            z: 0.5,
                        }),
                        orientation: Some(wire::Quaternion {
                            x: 0.0,
                            y: 0.0,
                            z: 0.0,
                            w: 1.0,
                        }),
                    }),
                    source_time_ms: 1,
                    maximum_age_ms: 1_000,
                    position_uncertainty_m: 0.001,
                    orientation_uncertainty_rad: 0.001,
                }),
                preconditions: Vec::new(),
                deadline_ms: 9_000,
            }),
        );
        authority_machine
            .receive_authenticated(proposal.clone(), 2, &authenticated)
            .unwrap();
        let response = process_coordination_message(
            &mut authority_machine,
            &mut engine,
            &proposal,
            &authenticated,
            2,
        )
        .unwrap()
        .unwrap();
        assert!(matches!(
            response.body,
            Some(Body::HandoffRecord(wire::HandoffRecord { state, .. }))
                if state == wire::HandoffState::Proposed as i32
        ));
        assert_eq!(
            engine.handoffs()["ump:handoff:zone"].authoritative_owner_machine_id,
            source
        );
        let impersonated = wire::HandoffUpdate {
            handoff_id: "ump:handoff:zone".into(),
            state: wire::HandoffState::Prepared.into(),
            previous_revision: 1,
            evidence: vec![wire::HandoffEvidence {
                actor_machine_id: destination.into(),
                evidence_type: "destination_claim".into(),
                evidence: b"forged".to_vec(),
                observed_at_ms: 2,
            }],
            reason: String::new(),
        };
        assert!(
            apply_handoff_update(&mut engine, &impersonated, source, 2)
                .unwrap_err()
                .to_string()
                .contains("authenticated peer")
        );
    }
}
