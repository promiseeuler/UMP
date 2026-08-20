#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
root=$(mktemp -d)
export PATH="$repo/target/debug:$PATH"
export UMP_S4_ROOT="$root"
"$repo/sim/s4/setup.sh" >/dev/null
UMP_DATA_DIR="$root/zone" umpd >"$root/zone.log" 2>&1 & zone_pid=$!
cleanup() {
  kill "$zone_pid" 2>/dev/null || true
  wait "$zone_pid" 2>/dev/null || true
}
trap cleanup EXIT
for _ in $(seq 1 100); do test -S "$root/zone/adapter.sock" && break; sleep 0.02; done
test -S "$root/zone/adapter.sock"

common=(--target 127.0.0.1:17443 --target-name zone.ump.local \
  --target-machine ump:machine:transfer-zone-1)
ump --data-dir "$root/arm" reserve-resource "${common[@]}" \
  --reservation-id s4-expiring-zone --task-id s4-handoff-fault \
  --resource-id ump:resource:s4-transfer-zone --duration-ms 2500 >/dev/null
ump --data-dir "$root/arm" propose-handoff "${common[@]}" \
  --handoff-id s4-expiring-handoff --task-id s4-handoff-fault \
  --destination ump:machine:mobile-base-1 --subject-id ump:subject:s4-package \
  --reservation-id s4-expiring-zone --reference-frame world \
  --subject-frame package/body --x 0 --y 1 --z 0.65 \
  --maximum-age-ms 10000 --deadline-after-ms 20000 >/dev/null
ump --data-dir "$root/arm" update-handoff "${common[@]}" \
  --handoff-id s4-expiring-handoff --state prepared --previous-revision 1 >/dev/null
ump --data-dir "$root/mobile" update-handoff "${common[@]}" \
  --handoff-id s4-expiring-handoff --state ready --previous-revision 2 >/dev/null
ump --data-dir "$root/arm" update-handoff "${common[@]}" \
  --handoff-id s4-expiring-handoff --state transferring --previous-revision 3 >/dev/null

sleep 2.6
status=$(ump --data-dir "$root/arm" query-handoff "${common[@]}" \
  --handoff-id s4-expiring-handoff)
case "$status" in *'"state":"unknown"'*) ;; *) echo "$status" >&2; exit 1 ;; esac
case "$status" in *'"inspection_required":true'*) ;; *) echo "$status" >&2; exit 1 ;; esac
case "$status" in *'"authoritative_owner_machine_id":"ump:machine:robot-arm-1"'*) ;;
  *) echo "$status" >&2; exit 1 ;;
esac
echo "S4 handoff fault passed: reservation loss during transfer preserved arm ownership as unknown."
