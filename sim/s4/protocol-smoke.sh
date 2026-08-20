#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
root=$(mktemp -d)
export PATH="$repo/target/debug:$PATH"
export UMP_S4_ROOT="$root"
export UMP_S4_TRACE="$root/trace.jsonl"
export UMP_S4_INSPECTOR_DIR="$root/inspector"

"$repo/sim/s4/setup.sh" >/dev/null
UMP_DATA_DIR="$root/mobile" umpd >"$root/mobile.log" 2>&1 & mobile_pid=$!
UMP_DATA_DIR="$root/arm" umpd >"$root/arm.log" 2>&1 & arm_pid=$!
UMP_DATA_DIR="$root/zone" umpd >"$root/zone.log" 2>&1 & zone_pid=$!

cleanup() {
  kill "$mobile_pid" "$arm_pid" "$zone_pid" "${mobile_adapter_pid:-}" "${arm_adapter_pid:-}" 2>/dev/null || true
  wait "$mobile_pid" "$arm_pid" "$zone_pid" "${mobile_adapter_pid:-}" "${arm_adapter_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT

for socket in "$root/mobile/adapter.sock" "$root/arm/adapter.sock" "$root/zone/adapter.sock"; do
  for _ in $(seq 1 100); do
    test -S "$socket" && break
    sleep 0.02
  done
  test -S "$socket"
done

python3 "$repo/sim/s4/fake_adapter.py" "$root/mobile/adapter.sock" 3 &
mobile_adapter_pid=$!
python3 "$repo/sim/s4/fake_adapter.py" "$root/arm/adapter.sock" 3 &
arm_adapter_pid=$!
"$repo/sim/s4/mission.sh"
wait "$mobile_adapter_pid" "$arm_adapter_pid"

test -s "$root/trace-invariants.json"
grep -q '"passed": true' "$root/trace-invariants.json"
grep -q '"passed": true' "$root/inspector/report.json"
echo "S4 protocol smoke passed: trace invariants prove six tasks and bilateral ownership commit."
