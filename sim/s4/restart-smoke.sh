#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
root=$(mktemp -d)
export PATH="$repo/target/debug:$PATH"
export UMP_S4_ROOT="$root"
"$repo/sim/s4/setup.sh" >/dev/null
common=(--target 127.0.0.1:17441 --target-name mobile.ump.local \
  --target-machine ump:machine:mobile-base-1)

start_daemon() {
  UMP_DATA_DIR="$root/mobile" umpd >>"$root/mobile.log" 2>&1 &
  daemon_pid=$!
  for _ in $(seq 1 100); do
    test -S "$root/mobile/adapter.sock" && break
    sleep 0.02
  done
  test -S "$root/mobile/adapter.sock"
}
stop_daemon() {
  kill "$daemon_pid"
  wait "$daemon_pid" 2>/dev/null || true
}
cleanup() {
  kill "${daemon_pid:-}" "${adapter_pid:-}" 2>/dev/null || true
  wait "${daemon_pid:-}" "${adapter_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT

start_daemon
ump --data-dir "$root/coordinator" issue-task "${common[@]}" \
  --task-id s4-restart-accepted --capability org.ump.navigation.navigate \
  --input-json '{"target_x":1.0,"target_y":0.0}' --lease-id lease-s4-mobile \
  --idempotency-key s4-restart-accepted >/dev/null
stop_daemon
start_daemon
python3 "$repo/sim/s4/fake_adapter.py" "$root/mobile/adapter.sock" 1 & adapter_pid=$!
for _ in $(seq 1 100); do
  accepted_status=$(ump --data-dir "$root/coordinator" query-task "${common[@]}" \
    --task-id s4-restart-accepted)
  case "$accepted_status" in *'"status":"succeeded"'*) break ;; esac
  sleep 0.02
done
wait "$adapter_pid"
case "$accepted_status" in *'"status":"succeeded"'*) ;; *) echo "$accepted_status" >&2; exit 1 ;; esac

python3 "$repo/sim/s4/fake_adapter.py" "$root/mobile/adapter.sock" 1 \
  --execution-seconds 10 & adapter_pid=$!
ump --data-dir "$root/coordinator" issue-task "${common[@]}" \
  --task-id s4-restart-running --capability org.ump.navigation.navigate \
  --input-json '{"target_x":2.0,"target_y":0.0}' --lease-id lease-s4-mobile \
  --idempotency-key s4-restart-running >/dev/null
for _ in $(seq 1 100); do
  running_status=$(ump --data-dir "$root/coordinator" query-task "${common[@]}" \
    --task-id s4-restart-running)
  case "$running_status" in *'"status":"running"'*) break ;; esac
  sleep 0.02
done
case "$running_status" in *'"status":"running"'*) ;; *) echo "$running_status" >&2; exit 1 ;; esac
stop_daemon
kill "$adapter_pid" 2>/dev/null || true
wait "$adapter_pid" 2>/dev/null || true
start_daemon
unknown_status=$(ump --data-dir "$root/coordinator" query-task "${common[@]}" \
  --task-id s4-restart-running)
case "$unknown_status" in *'"status":"unknown"'*) ;; *) echo "$unknown_status" >&2; exit 1 ;; esac
case "$unknown_status" in *'"inspection_required":true'*) ;; *) echo "$unknown_status" >&2; exit 1 ;; esac
echo "S4 restart smoke passed: accepted work resumed; running work recovered as unknown."
