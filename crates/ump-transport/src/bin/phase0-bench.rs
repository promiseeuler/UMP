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
    tls_tcp::{accept, bind_loopback, connect},
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
struct BenchmarkReport {
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
    let listener = bind_loopback().await?;
    let address = listener.local_addr()?;
    let startup_to_listening = startup_started.elapsed();

    let server = tokio::spawn(async move {
        for connection_index in 0..=RECONNECT_SAMPLES {
            let mut stream = accept(&listener, server_config.clone()).await?;
            let requests = if connection_index == 0 {
                WARMUP_REQUESTS + MEASURED_REQUESTS
            } else {
                1
            };
            for _ in 0..requests {
                let envelope = read_envelope(&mut stream).await?;
                write_envelope(&mut stream, &envelope).await?;
            }
        }
        Ok::<(), Box<dyn std::error::Error + Send + Sync>>(())
    });

    let initial_connect_started = Instant::now();
    let mut stream = connect(address, "arm-1.ump.local", client_config.clone()).await?;
    let initial_connection = initial_connect_started.elapsed();
    let mut machine = Machine::new("ump:machine:base-1", "benchmark-session");
    let request = machine.hello(0);
    let encoded_frame_bytes = request.encoded_len() + size_of::<u32>();

    for _ in 0..WARMUP_REQUESTS {
        round_trip(&mut stream, &request).await?;
    }

    let mut request_samples = Vec::with_capacity(MEASURED_REQUESTS);
    for _ in 0..MEASURED_REQUESTS {
        let started = Instant::now();
        round_trip(&mut stream, &request).await?;
        request_samples.push(started.elapsed());
    }
    drop(stream);

    let mut reconnect_samples = Vec::with_capacity(RECONNECT_SAMPLES);
    for _ in 0..RECONNECT_SAMPLES {
        let started = Instant::now();
        let mut stream = connect(address, "arm-1.ump.local", client_config.clone()).await?;
        round_trip(&mut stream, &request).await?;
        reconnect_samples.push(started.elapsed());
    }

    server.await??;
    let executable = std::env::current_exe()?;
    let binary_bytes = std::fs::metadata(executable)?.len();
    let request_response = distribution(request_samples);
    let reconnect_request_response = distribution(reconnect_samples);
    let report = BenchmarkReport {
        benchmark: "UMP Phase 0 authenticated request/response",
        transport: "TLS 1.3 over TCP/IPv4 loopback",
        authentication: "development CA with mutual leaf certificates",
        build_profile: "release",
        architecture: std::env::consts::ARCH.into(),
        operating_system: os_description(),
        rust_version: command_output("rustc", &["--version"]),
        source_revision: command_output("git", &["rev-parse", "--short", "HEAD"]),
        worktree_dirty: !command_output("git", &["status", "--porcelain"]).is_empty(),
        warmup_requests: WARMUP_REQUESTS,
        measured_requests: MEASURED_REQUESTS,
        encoded_frame_bytes,
        startup_to_listening_us: micros(startup_to_listening),
        binary_bytes,
        resident_memory_kib: resident_memory_kib(),
        initial_connection_us: micros(initial_connection),
        request_response,
        reconnect_request_response,
        passed: true,
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

async fn round_trip<S>(
    stream: &mut S,
    request: &ump_protocol::v1::Envelope,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    write_envelope(stream, request).await?;
    let response = read_envelope(stream).await?;
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

fn os_description() -> String {
    let system = command_output("uname", &["-s"]);
    let release = command_output("uname", &["-r"]);
    format!("{system} {release}")
}

fn resident_memory_kib() -> Option<u64> {
    let pid = std::process::id().to_string();
    let output = command_output("ps", &["-o", "rss=", "-p", &pid]);
    output.trim().parse().ok()
}
