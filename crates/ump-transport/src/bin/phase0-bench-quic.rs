#![forbid(unsafe_code)]

use std::{
    process::Command,
    time::{Duration, Instant},
};

use prost::Message;
use serde::Serialize;
use ump_runtime::Machine;
use ump_transport::{
    credentials::DevelopmentPki,
    framing::{read_envelope, write_envelope},
    quic::{client_endpoint, server_endpoint},
};

const WARMUP_REQUESTS: usize = 100;
const MEASURED_REQUESTS: usize = 1_000;
const RECONNECT_SAMPLES: usize = 100;

#[derive(Serialize)]
struct Distribution {
    samples: usize,
    p50_us: u64,
    p95_us: u64,
    p99_us: u64,
    maximum_us: u64,
}

#[derive(Serialize)]
struct Report {
    benchmark: &'static str,
    transport: &'static str,
    authentication: &'static str,
    build_profile: &'static str,
    architecture: String,
    operating_system: String,
    rust_version: String,
    source_revision: String,
    worktree_dirty: bool,
    warmup_requests: usize,
    measured_requests: usize,
    encoded_frame_bytes: usize,
    startup_to_listening_us: u64,
    binary_bytes: u64,
    resident_memory_kib: Option<u64>,
    initial_connection_us: u64,
    request_response: Distribution,
    reconnect_request_response: Distribution,
    passed: bool,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if cfg!(debug_assertions) {
        return Err("benchmark MUST run with --release".into());
    }

    let startup_started = Instant::now();
    let pki = DevelopmentPki::generate()?;
    let server_identity = pki.issue("arm-1.ump.local")?;
    let client_identity = pki.issue("base-1.ump.local")?;
    let server_config = pki.server_config(&server_identity)?;
    let client_config = pki.client_config(&client_identity)?;
    let server_endpoint =
        server_endpoint((std::net::Ipv4Addr::LOCALHOST, 0).into(), server_config)?;
    let address = server_endpoint.local_addr()?;
    let client_endpoint = client_endpoint(client_config)?;
    let startup_to_listening = startup_started.elapsed();

    let server = tokio::spawn(async move {
        for connection_index in 0..=RECONNECT_SAMPLES {
            let connection = server_endpoint
                .accept()
                .await
                .ok_or("QUIC endpoint closed")?
                .await?;
            let (mut send, mut receive) = connection.accept_bi().await?;
            let requests = if connection_index == 0 {
                WARMUP_REQUESTS + MEASURED_REQUESTS
            } else {
                1
            };
            for _ in 0..requests {
                let envelope = read_envelope(&mut receive).await?;
                write_envelope(&mut send, &envelope).await?;
            }
            send.finish()?;
            send.stopped().await?;
        }
        Ok::<(), Box<dyn std::error::Error + Send + Sync>>(())
    });

    let initial_connect_started = Instant::now();
    let connection = client_endpoint.connect(address, "arm-1.ump.local")?.await?;
    let initial_connection = initial_connect_started.elapsed();
    let (mut send, mut receive) = connection.open_bi().await?;
    let mut machine = Machine::new("ump:machine:base-1", "benchmark-session");
    let request = machine.hello(0);
    let encoded_frame_bytes = request.encoded_len() + size_of::<u32>();

    for _ in 0..WARMUP_REQUESTS {
        round_trip(&mut send, &mut receive, &request).await?;
    }
    let mut request_samples = Vec::with_capacity(MEASURED_REQUESTS);
    for _ in 0..MEASURED_REQUESTS {
        let started = Instant::now();
        round_trip(&mut send, &mut receive, &request).await?;
        request_samples.push(started.elapsed());
    }
    send.finish()?;
    receive.read_to_end(0).await?;
    connection.close(0_u8.into(), b"benchmark connection complete");

    let mut reconnect_samples = Vec::with_capacity(RECONNECT_SAMPLES);
    for _ in 0..RECONNECT_SAMPLES {
        let started = Instant::now();
        let connection = client_endpoint.connect(address, "arm-1.ump.local")?.await?;
        let (mut send, mut receive) = connection.open_bi().await?;
        write_envelope(&mut send, &request).await?;
        send.finish()?;
        let response = read_envelope(&mut receive).await?;
        if response.message_id != request.message_id {
            return Err("response message identifier mismatch".into());
        }
        receive.read_to_end(0).await?;
        reconnect_samples.push(started.elapsed());
        connection.close(0_u8.into(), b"benchmark reconnect complete");
    }

    server.await??;
    let report = Report {
        benchmark: "UMP Phase 0 authenticated request/response",
        transport: "QUIC over UDP/IPv4 loopback",
        authentication: "TLS 1.3 development CA with mutual leaf certificates",
        build_profile: "release",
        architecture: std::env::consts::ARCH.into(),
        operating_system: format!(
            "{} {}",
            command_output("uname", &["-s"]),
            command_output("uname", &["-r"])
        ),
        rust_version: command_output("rustc", &["--version"]),
        source_revision: command_output("git", &["rev-parse", "--short", "HEAD"]),
        worktree_dirty: !command_output("git", &["status", "--porcelain"]).is_empty(),
        warmup_requests: WARMUP_REQUESTS,
        measured_requests: MEASURED_REQUESTS,
        encoded_frame_bytes,
        startup_to_listening_us: micros(startup_to_listening),
        binary_bytes: std::fs::metadata(std::env::current_exe()?)?.len(),
        resident_memory_kib: resident_memory_kib(),
        initial_connection_us: micros(initial_connection),
        request_response: distribution(request_samples),
        reconnect_request_response: distribution(reconnect_samples),
        passed: true,
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

async fn round_trip(
    send: &mut quinn::SendStream,
    receive: &mut quinn::RecvStream,
    request: &ump_protocol::v1::Envelope,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    write_envelope(send, request).await?;
    let response = read_envelope(receive).await?;
    if response.message_id != request.message_id {
        return Err("response message identifier mismatch".into());
    }
    Ok(())
}

fn distribution(mut samples: Vec<Duration>) -> Distribution {
    samples.sort_unstable();
    Distribution {
        samples: samples.len(),
        p50_us: percentile(&samples, 50),
        p95_us: percentile(&samples, 95),
        p99_us: percentile(&samples, 99),
        maximum_us: micros(*samples.last().expect("benchmark has samples")),
    }
}

fn percentile(samples: &[Duration], percentile: usize) -> u64 {
    let index = (samples.len() * percentile).div_ceil(100).saturating_sub(1);
    micros(samples[index])
}

fn micros(duration: Duration) -> u64 {
    duration.as_micros().try_into().unwrap_or(u64::MAX)
}

fn command_output(program: &str, arguments: &[&str]) -> String {
    Command::new(program)
        .args(arguments)
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_else(|| "unknown".into())
}

fn resident_memory_kib() -> Option<u64> {
    let pid = std::process::id().to_string();
    command_output("ps", &["-o", "rss=", "-p", &pid])
        .trim()
        .parse()
        .ok()
}
