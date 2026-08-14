#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
root=$(mktemp -d)
export PATH="$repo/target/debug:$PATH"
export UMP_S4_ROOT="$root"
export UMP_S4_LEASE_DURATION_MS=3000
"$repo/sim/s4/setup.sh" >/dev/null

UMP_DATA_DIR="$root/mobile" umpd >"$root/mobile.log" 2>&1 & daemon_pid=$!
cleanup() {
  kill "$daemon_pid" "${adapter_pid:-}" 2>/dev/null || true
  wait "$daemon_pid" "${adapter_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT
for _ in $(seq 1 100); do
  test -S "$root/mobile/adapter.sock" && break
  sleep 0.02
done
test -S "$root/mobile/adapter.sock"
python3 "$repo/sim/s4/fake_adapter.py" "$root/mobile/adapter.sock" 1 \
  --execution-seconds 10 & adapter_pid=$!

common=(--target 127.0.0.1:17441 --target-name mobile.ump.local \
  --target-machine ump:machine:mobile-base-1)
ump --data-dir "$root/coordinator" issue-task "${common[@]}" \
  --task-id s4-lease-expiry --capability org.ump.navigation.navigate \
  --input-json '{"target_x":3.0,"target_y":0.0}' --lease-id lease-s4-mobile \
  --idempotency-key s4-lease-expiry >/dev/null

for _ in $(seq 1 250); do
  status=$(ump --data-dir "$root/coordinator" query-task "${common[@]}" \
    --task-id s4-lease-expiry)
  case "$status" in *'"status":"unknown"'*) break ;; esac
  sleep 0.02
done
wait "$adapter_pid"
case "$status" in *'"status":"unknown"'*) ;; *) echo "$status" >&2; exit 1 ;; esac
case "$status" in *'"inspection_required":true'*) ;; *) echo "$status" >&2; exit 1 ;; esac
echo "S4 lease-expiry smoke passed: authority loss stopped work and required inspection."
