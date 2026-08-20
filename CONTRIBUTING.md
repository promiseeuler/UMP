# Contributing to UMP

UMP is specification-led. Behavioral changes must update the normative text, schemas, reference implementation, and conformance tests together.

## Development setup

Install Rust using `rustup`. A system Protocol Buffers compiler is not required; the build uses a vendored compiler.

```sh
cargo test --workspace
cargo run -p ump-sim --bin s0
cargo run --release -p ump-sim --bin s1
make sim-s4-protocol
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
PYTHONPATH=sdk/python python3 -m unittest discover -s tests/python
```

## Change process

- Small compatible corrections may use a normal pull request.
- New protocol behavior or compatibility changes require a UMP Enhancement Proposal in `docs/uep/`.
- Every normative requirement uses a stable identifier and MUST/SHOULD/MAY terminology.
- Physical-effect features must include security, safety, restart, and fault behavior.
- Commits must not include generated build output or credentials.

## Compatibility

Never reuse a Protocol Buffers field number. Additive optional fields may be introduced in a minor release. Incompatible wire or semantic changes require a protocol major version.
