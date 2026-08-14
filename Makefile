SOURCE_REVISION ?= $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
SOURCE_DIRTY ?= $(shell test -n "$$(git status --porcelain 2>/dev/null)" && echo true || echo false)
PACKAGE_VERSION ?= $(shell sed -n 's/^version = "\([^"]*\)"/\1/p' Cargo.toml | head -1)
ROS_ARCH ?= amd64
ROS_PLATFORM ?= linux/amd64

.PHONY: benchmark-phase0 check test test-vectors python-bindings vendor-bridge-test gateway-test sim-s0 sim-s1 sim-s2 sim-s3 sim-s4-protocol sim-s5 ros2-build ros2-test ros2-package-build ros2-package-test ros2-s5-package ros2-smoke ros2-model-swap s4-build ros2-s4 ros2-s4-model-swap ros2-s4-faults native-package native-package-test native-bundle container-build container-test

benchmark-phase0:
	cargo run --release -p ump-transport --bin phase0-bench
	cargo run --release -p ump-transport --bin phase0-bench-quic

native-package:
	cargo build --locked --release -p ump-cli --bins
	packaging/native/build-deb.sh

native-bundle:
	cargo build --locked --release -p ump-cli --bins
	packaging/native/build-bundle.sh
	packaging/native/test-bundle.sh "$$(find dist -maxdepth 1 -name 'ump_*_linux_*.tar.gz' -type f | sort | tail -1)"

native-package-test: native-package
	python3 -m unittest discover -s packaging -p 'test_*.py'
	packaging/native/test-deb.sh "$$(find dist -maxdepth 1 -name 'ump_*.deb' -type f | sort | tail -1)"
	@lifecycle_dir=$$(mktemp -d); trap 'rm -rf "$$lifecycle_dir"' EXIT; \
	  version=$$(dpkg-deb --field "$$(find dist -maxdepth 1 -name 'ump_*.deb' -type f | sort | tail -1)" Version); \
	  UMP_PACKAGE_OUTPUT_DIR="$$lifecycle_dir" UMP_PACKAGE_VERSION="$$version" packaging/native/build-deb.sh >/dev/null; \
	  UMP_PACKAGE_OUTPUT_DIR="$$lifecycle_dir" UMP_PACKAGE_VERSION="$$version+lifecycle1" packaging/native/build-deb.sh >/dev/null; \
	  packaging/native/test-lifecycle.sh \
	    "$$lifecycle_dir/ump_$${version}_$$(dpkg --print-architecture).deb" \
	    "$$lifecycle_dir/ump_$${version}+lifecycle1_$$(dpkg --print-architecture).deb"

container-build:
	docker build -f packaging/container/Dockerfile -t ump:local .

container-test: container-build
	packaging/container/test.sh ump:local

check:
	cargo fmt --all --check
	cargo clippy --workspace --all-targets -- -D warnings
	cargo test --workspace
	PYTHONPATH=sdk/python python3 -m unittest discover -s tests/python
	c++ -std=c++17 -Wall -Wextra -Werror -fsyntax-only -I sdk/cpp/include tests/cpp/adapter_smoke.cpp

test:
	cargo test --workspace

test-vectors:
	cargo run -p ump-protocol --example generate_vectors
	cargo test -p ump-protocol --test golden_vectors
	PYTHONPATH=sdk/python python3 -m unittest discover -s tests/python

python-bindings:
	cargo run -p ump-protocol --example generate_python

vendor-bridge-test:
	python3 -m py_compile bridge/http/ump_http_bridge.py bridge/http/example_controller.py
	python3 -m unittest -v bridge/http/test_http_bridge.py

gateway-test: vendor-bridge-test
	python3 -m py_compile gateway/ump_gateway.py
	python3 -m unittest -v gateway/test_ump_gateway.py

sim-s0:
	cargo run -p ump-sim --bin s0

sim-s1:
	cargo run --release -p ump-sim --bin s1

sim-s2:
	cargo run --release -p ump-sim --bin s2

sim-s3:
	cargo run --release -p ump-sim --bin s3

sim-s4-protocol:
	cargo build -p ump-cli --bins
	PYTHONPATH=sim/s4 python3 -m unittest sim/s4/test_verify_trace.py
	PYTHONPATH=sim/s4 python3 -m unittest sim/s4/test_verify_inspector.py
	PYTHONPATH=sim/s4 python3 -m unittest sim/s4/test_performance.py
	./sim/s4/protocol-smoke.sh
	./sim/s4/cancellation-smoke.sh
	./sim/s4/restart-smoke.sh
	./sim/s4/lease-expiry-smoke.sh
	./sim/s4/network-fault-smoke.sh
	./sim/s4/safety-state-smoke.sh
	./sim/s4/handoff-fault-smoke.sh
	./sim/s4/zone-intrusion-smoke.sh
	./sim/s4/minor-version-smoke.sh

sim-s5:
	cargo build -p ump-cli --bins
	./sim/s5/run.sh

ros2-build:
	docker build --platform $(ROS_PLATFORM) -f sim/ros2/Dockerfile -t ump-ros2:phase4 .

ros2-test: ros2-build
	docker run --rm --platform linux/amd64 ump-ros2:phase4 bash -lc 'colcon test --merge-install --event-handlers console_direct+ && colcon test-result --verbose'

ros2-package-build: ros2-build
	docker build --platform $(ROS_PLATFORM) -f packaging/ros2/Dockerfile -t ump-ros2-package:phase5 .
	mkdir -p dist
	docker run --rm --platform $(ROS_PLATFORM) -v "$(CURDIR)/dist:/dist" \
	  -e UMP_PACKAGE_VERSION=$(PACKAGE_VERSION) -e UMP_PACKAGE_ARCH=$(ROS_ARCH) \
	  -e UMP_SOURCE_REVISION="$(SOURCE_REVISION)" ump-ros2-package:phase5

ros2-package-test: ros2-package-build
	UMP_PACKAGE_ARCH_OVERRIDE=$(ROS_ARCH) packaging/ros2/test-deb.sh \
	  dist/ump-ros2-jazzy_$(PACKAGE_VERSION)_$(ROS_ARCH).deb

ros2-s5-package: ros2-package-test
	UMP_PACKAGE_ARCH_OVERRIDE=$(ROS_ARCH) packaging/ros2/test-s5-deb.sh \
	  dist/ump-ros2-jazzy_$(PACKAGE_VERSION)_$(ROS_ARCH).deb

ros2-smoke: ros2-build
	docker run --rm --platform linux/amd64 ump-ros2:phase4 /workspaces/ump_ros2/sim-smoke.sh

ros2-model-swap: ros2-build
	docker run --rm --platform linux/amd64 \
		-e UMP_MOBILE_MODEL_VARIANT=alternate \
		ump-ros2:phase4 /workspaces/ump_ros2/sim-smoke.sh

s4-build: ros2-build
	docker build --platform linux/amd64 -f sim/s4/Dockerfile -t ump-s4:phase4 .

ros2-s4: s4-build
	mkdir -p artifacts/s4
	docker run --rm --platform linux/amd64 \
		-v "$(CURDIR)/artifacts/s4:/artifacts" \
		-e UMP_S4_TRACE=/artifacts/trace.jsonl \
		-e UMP_S4_REPORT=/artifacts/invariants.json \
		-e UMP_S4_INSPECTOR_DIR=/artifacts/inspector \
		-e UMP_S4_PERFORMANCE_REPORT=/artifacts/performance.json \
		-e UMP_SOURCE_REVISION="$(SOURCE_REVISION)" \
		-e UMP_SOURCE_DIRTY="$(SOURCE_DIRTY)" \
		-e UMP_S4_LOG_DIR=/artifacts/logs \
		ump-s4:phase4 /workspaces/ump_ros2/s4/run.sh

ros2-s4-model-swap: s4-build
	mkdir -p artifacts/s4-model-swap
	docker run --rm --platform linux/amd64 \
		-v "$(CURDIR)/artifacts/s4-model-swap:/artifacts" \
		-e UMP_MOBILE_MODEL_VARIANT=alternate \
		-e UMP_S4_TRACE=/artifacts/trace.jsonl \
		-e UMP_S4_REPORT=/artifacts/invariants.json \
		-e UMP_S4_INSPECTOR_DIR=/artifacts/inspector \
		-e UMP_S4_PERFORMANCE_REPORT=/artifacts/performance.json \
		-e UMP_SOURCE_REVISION="$(SOURCE_REVISION)" \
		-e UMP_SOURCE_DIRTY="$(SOURCE_DIRTY)" \
		-e UMP_S4_LOG_DIR=/artifacts/logs \
		ump-s4:phase4 /workspaces/ump_ros2/s4/run.sh

ros2-s4-faults: s4-build
	mkdir -p artifacts/s4-faults
	docker run --rm --platform linux/amd64 \
		-v "$(CURDIR)/artifacts/s4-faults:/artifacts" \
		-e UMP_S4_INSPECTOR_DIR=/artifacts/inspector \
		-e UMP_S4_LOG_DIR=/artifacts/logs \
		ump-s4:phase4 /workspaces/ump_ros2/s4/embodied-faults.sh
