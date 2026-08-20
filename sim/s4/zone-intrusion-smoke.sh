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
for _ in $(seq 1 100); do
  test -S "$root/zone/adapter.sock" && break
  sleep 0.02
done
test -S "$root/zone/adapter.sock"

common=(--target 127.0.0.1:17443 --target-name zone.ump.local \
  --target-machine ump:machine:transfer-zone-1)
ump --data-dir "$root/arm" reserve-resource "${common[@]}" \
  --reservation-id s4-intruded-zone --task-id s4-intrusion-handoff \
  --resource-id ump:resource:s4-transfer-zone --duration-ms 60000 >/dev/null
ump --data-dir "$root/arm" propose-handoff "${common[@]}" \
  --handoff-id s4-intrusion-handoff --task-id s4-intrusion-handoff \
  --destination ump:machine:mobile-base-1 --subject-id ump:subject:s4-package \
  --reservation-id s4-intruded-zone --reference-frame world \
  --subject-frame package/body --x 0 --y 1 --z 0.65 \
  --maximum-age-ms 10000 --deadline-after-ms 20000 >/dev/null
ump --data-dir "$root/arm" update-handoff "${common[@]}" \
  --handoff-id s4-intrusion-handoff --state prepared --previous-revision 1 >/dev/null
ump --data-dir "$root/mobile" update-handoff "${common[@]}" \
  --handoff-id s4-intrusion-handoff --state ready --previous-revision 2 >/dev/null
ump --data-dir "$root/arm" update-handoff "${common[@]}" \
  --handoff-id s4-intrusion-handoff --state transferring --previous-revision 3 >/dev/null

revocation=$(python3 "$repo/sim/s4/local_api_call.py" \
  "$root/zone/adapter.sock" resource_intrusion \
  '{"resource_id":"ump:resource:s4-transfer-zone","reason":"zone_intrusion"}')
case "$revocation" in *'"revoked_reservation_ids":["s4-intruded-zone"]'*) ;;
  *) echo "$revocation" >&2; exit 1 ;;
esac

status=$(ump --data-dir "$root/arm" query-handoff "${common[@]}" \
  --handoff-id s4-intrusion-handoff)
case "$status" in *'"state":"unknown"'*) ;; *) echo "$status" >&2; exit 1 ;; esac
case "$status" in *'"inspection_required":true'*) ;; *) echo "$status" >&2; exit 1 ;; esac
case "$status" in *'"authoritative_owner_machine_id":"ump:machine:robot-arm-1"'*) ;;
  *) echo "$status" >&2; exit 1 ;;
esac
echo "S4 zone-intrusion smoke passed: local sensor revocation preserved arm ownership as unknown."
