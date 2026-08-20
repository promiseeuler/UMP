#![forbid(unsafe_code)]

pub mod credentials;
pub mod framing;
pub mod quic;
pub mod tls_tcp;

pub const MAX_CONTROL_FRAME_BYTES: usize = 1024 * 1024;
