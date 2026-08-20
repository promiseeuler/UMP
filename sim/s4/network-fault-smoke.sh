#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
root=$(mktemp -d)
export PATH="$repo/target/debug:$PATH"
export UMP_S4_ROOT="$root"
"$repo/sim/s4/setup.sh" >/dev/null
cleanup() {
  kill "${daemon_pid:-}" "${proxy_pid:-}" "${adapter_pid:-}" 2>/dev/null || true
  wait "${daemon_pid:-}" "${proxy_pid:-}" "${adapter_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT

run_profile() {
  local task=$1 listen_port=$2
  shift 2
  echo "running network profile: $task"
  rm -f "$root/mobile/adapter.sock"
  UMP_DATA_DIR="$root/mobile" umpd >"$root/$task-mobile.log" 2>&1 & daemon_pid=$!
  for _ in $(seq 1 100); do
    test -S "$root/mobile/adapter.sock" && break
    sleep 0.02
  done
  test -S "$root/mobile/adapter.sock"
  python3 "$repo/sim/s4/fake_adapter.py" "$root/mobile/adapter.sock" 1 & adapter_pid=$!
  python3 "$repo/sim/s4/udp_fault_proxy.py" --listen-port "$listen_port" \
    --server-port 17441 "$@" & proxy_pid=$!
  sleep 0.1
  common=(--target "127.0.0.1:$listen_port" --target-name mobile.ump.local \
    --target-machine ump:machine:mobile-base-1)
  ump --data-dir "$root/coordinator" issue-task "${common[@]}" \
    --task-id "$task" --capability org.ump.navigation.navigate \
    --input-json '{"target_x":1.0,"target_y":1.0}' --lease-id lease-s4-mobile \
    --idempotency-key "$task" --wait >/dev/null
  wait "$adapter_pid"
  unset adapter_pid
  kill "$proxy_pid"; wait "$proxy_pid" 2>/dev/null || true
  unset proxy_pid
  kill "$daemon_pid"; wait "$daemon_pid" 2>/dev/null || true
  unset daemon_pid
}

run_profile s4-network-loss-5 17541 --delay-ms 50 --drop-every 20
run_profile s4-network-loss-10 17542 --delay-ms 100 --drop-every 10
run_profile s4-network-burst 17543 --delay-ms 250 --burst-every 20 --burst-length 2
run_profile s4-network-latency 17544 --delay-ms 500 --jitter-ms 100
echo "S4 network smoke passed: 5%, 10%, burst loss, and 500 ms latency/jitter profiles."
