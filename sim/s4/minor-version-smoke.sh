#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
root=$(mktemp -d)
export PATH="$repo/target/debug:$PATH"
export UMP_S4_ROOT="$root"
export UMP_S4_ARM_PROTOCOL_MINOR=1
"$repo/sim/s4/setup.sh" >/dev/null
UMP_DATA_DIR="$root/arm" umpd >"$root/arm.log" 2>&1 & arm_pid=$!
cleanup() {
  kill "$arm_pid" "${adapter_pid:-}" 2>/dev/null || true
  wait "$arm_pid" "${adapter_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT
for _ in $(seq 1 100); do
  test -S "$root/arm/adapter.sock" && break
  sleep 0.02
done
test -S "$root/arm/adapter.sock"
python3 "$repo/sim/s4/fake_adapter.py" "$root/arm/adapter.sock" 1 & adapter_pid=$!

ump --data-dir "$root/coordinator" issue-task \
  --target 127.0.0.1:17442 --target-name arm.ump.local \
  --target-machine ump:machine:robot-arm-1 \
  --task-id s4-minor-compatibility --capability org.ump.manipulation.arm_pose \
  --input-json '{"shoulder_rad":0.2,"elbow_rad":-0.4}' \
  --lease-id lease-s4-arm --idempotency-key s4-minor-compatibility --wait >/dev/null
wait "$adapter_pid"
unset adapter_pid

peers=$(ump --data-dir "$root/arm" peers)
case "$peers" in *'"selected_minor": 1'*) ;; *) echo "$peers" >&2; exit 1 ;; esac
doctor=$(ump --data-dir "$root/arm" doctor)
case "$doctor" in *'"protocol_minor":1'*) ;; *) echo "$doctor" >&2; exit 1 ;; esac
echo "S4 minor-version smoke passed: protocol 1.2 coordinator completed work with a negotiated 1.1 arm."
