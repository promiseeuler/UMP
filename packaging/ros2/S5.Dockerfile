FROM rust:1.85-bookworm AS ump-runtime-builder

WORKDIR /src
RUN apt-get update \
    && apt-get install -y --no-install-recommends dpkg-dev \
    && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock LICENSE ./
COPY crates crates
COPY schemas schemas
COPY packaging/native packaging/native
COPY packaging/debian packaging/debian
COPY packaging/systemd packaging/systemd
RUN --mount=type=cache,id=ump-s5-cargo-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=ump-s5-cargo-target,target=/src/target \
    cargo build --locked --release -p ump-cli --bins \
    && UMP_BINARY_DIR=/src/target/release UMP_PACKAGE_OUTPUT_DIR=/runtime-package \
       packaging/native/build-deb.sh >/dev/null

FROM ump-ros2:phase4

ARG ROS_DEB
COPY --from=ump-runtime-builder /runtime-package/ump_*.deb /tmp/ump.deb
COPY ${ROS_DEB} /tmp/ump-ros2-jazzy.deb
RUN dpkg -i /tmp/ump.deb /tmp/ump-ros2-jazzy.deb >/dev/null \
    && rm /tmp/ump.deb /tmp/ump-ros2-jazzy.deb

COPY bridge /opt/ump/bridge
COPY gateway /opt/ump/gateway
COPY sim/s4 /opt/ump/sim/s4
COPY sim/s5 /opt/ump/sim/s5

ENTRYPOINT []
CMD ["bash"]
