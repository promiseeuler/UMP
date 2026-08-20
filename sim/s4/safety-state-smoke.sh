#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
root=$(mktemp -d)
export PATH="$repo/target/debug:$PATH"
export UMP_S4_ROOT="$root"
"$repo/sim/s4/setup.sh" >/dev/null
UMP_DATA_DIR="$root/mobile" umpd >"$root/mobile.log" 2>&1 & daemon_pid=$!
cleanup() {
  kill "$daemon_pid" "${adapter_pid:-}" 2>/dev/null || true
  wait "$daemon_pid" "${adapter_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT
socket="$root/mobile/adapter.sock"
for _ in $(seq 1 100); do test -S "$socket" && break; sleep 0.02; done
test -S "$socket"

common=(--target 127.0.0.1:17441 --target-name mobile.ump.local \
  --target-machine ump:machine:mobile-base-1)
ump --data-dir "$root/coordinator" issue-task "${common[@]}" \
  --task-id s4-safety-gate --capability org.ump.navigation.navigate \
  --input-json '{"target_x":1.0,"target_y":0.0}' --lease-id lease-s4-mobile \
  --idempotency-key s4-safety-gate >/dev/null

python3 "$repo/sim/s4/local_api_call.py" "$socket" state_update \
  '{"operational":"faulted","safety":"emergency_stop","revision":2,"source_time_ms":1}' >/dev/null
python3 "$repo/sim/s4/fake_adapter.py" "$socket" 1 & adapter_pid=$!
sleep 0.3
status=$(ump --data-dir "$root/coordinator" query-task "${common[@]}" --task-id s4-safety-gate)
case "$status" in *'"status":"accepted"'*) ;; *) echo "$status" >&2; exit 1 ;; esac

python3 "$repo/sim/s4/local_api_call.py" "$socket" state_update \
  '{"operational":"idle","safety":"normal","revision":3,"source_time_ms":2}' >/dev/null
for _ in $(seq 1 100); do
  status=$(ump --data-dir "$root/coordinator" query-task "${common[@]}" --task-id s4-safety-gate)
  case "$status" in *'"status":"succeeded"'*) break ;; esac
  sleep 0.02
done
wait "$adapter_pid"
case "$status" in *'"status":"succeeded"'*) ;; *) echo "$status" >&2; exit 1 ;; esac
echo "S4 safety-state smoke passed: emergency state blocked claim until normal revision."
