.PHONY: check test sim-s0

check:
	cargo fmt --all --check
	cargo clippy --workspace --all-targets -- -D warnings
	cargo test --workspace

test:
	cargo test --workspace

sim-s0:
	cargo run -p ump-sim --bin s0

