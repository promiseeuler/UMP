FROM rust:1.85.0-bookworm AS builder

WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY crates crates
COPY schemas schemas
RUN cargo build --locked --release -p ump-cli --bins

FROM debian:bookworm-slim
LABEL org.opencontainers.image.title="Universal Machine Protocol runtime" \
      org.opencontainers.image.source="https://github.com/promiseeuler/UMP" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN groupadd --gid 65532 ump \
    && useradd --uid 65532 --gid 65532 --home-dir /var/lib/ump \
       --no-create-home --shell /usr/sbin/nologin ump \
    && install -d -o 65532 -g 65532 -m 0700 /var/lib/ump
COPY --from=builder /build/target/release/ump /usr/local/bin/ump
COPY --from=builder /build/target/release/umpd /usr/local/bin/umpd
USER 65532:65532
ENV UMP_DATA_DIR=/var/lib/ump
VOLUME ["/var/lib/ump"]
ENTRYPOINT ["/usr/local/bin/umpd"]
