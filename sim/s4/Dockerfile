FROM rust:1.85.0-bookworm AS ump-builder

WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY crates crates
COPY schemas schemas
RUN cargo build --release -p ump-cli --bins

FROM ump-ros2:phase4
COPY --from=ump-builder /build/target/release/ump /usr/local/bin/ump
COPY --from=ump-builder /build/target/release/umpd /usr/local/bin/umpd
COPY sim/s4 /workspaces/ump_ros2/s4
