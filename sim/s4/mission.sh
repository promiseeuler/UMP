#!/usr/bin/env bash
set -euo pipefail

root=${UMP_S4_ROOT:-/run/ump}
trace=${UMP_S4_TRACE:-/tmp/ump-s4-trace.jsonl}
report=${UMP_S4_REPORT:-${trace%.jsonl}-invariants.json}

issue() {
  local executor=$1 name=$2 machine=$3 lease=$4 task=$5 capability=$6 input=$7
  ump --data-dir "$root/coordinator" issue-task \
    --target "$executor" --target-name "$name" --target-machine "$machine" \
    --task-id "$task" --capability "$capability" --input-json "$input" \
    --lease-id "$lease" --idempotency-key "$task" --deadline-after-ms 90000 \
    | tee -a "$trace"
}

wait_task() {
  local executor=$1 name=$2 machine=$3 task=$4
  local response
  for _ in $(seq 1 900); do
    if ! response=$(ump --data-dir "$root/coordinator" query-task \
      --target "$executor" --target-name "$name" --target-machine "$machine" \
      --task-id "$task"); then
      sleep 0.1
      continue
    fi
    case "$response" in
      *'"status":"succeeded"'*) printf '%s\n' "$response" | tee -a "$trace"; return ;;
      *'"status":"failed"'*|*'"status":"cancelled"'*|*'"status":"rejected"'*|*'"status":"unknown"'*)
        printf '%s\n' "$response" | tee -a "$trace"; return 1 ;;
    esac
    sleep 0.1
  done
  echo "task $task did not reach a terminal state" >&2
  return 1
}

mobile=127.0.0.1:17441
arm=127.0.0.1:17442
zone=127.0.0.1:17443
: >"$trace"

issue "$arm" arm.ump.local ump:machine:robot-arm-1 lease-s4-arm \
  s4-arm-pick org.ump.payload.attach '{}'
wait_task "$arm" arm.ump.local ump:machine:robot-arm-1 s4-arm-pick

issue "$mobile" mobile.ump.local ump:machine:mobile-base-1 lease-s4-mobile \
  s4-mobile-approach org.ump.navigation.navigate \
  '{"target_x":0.0,"target_y":1.0,"tolerance_m":0.18,"max_speed_mps":0.7}'
wait_task "$mobile" mobile.ump.local ump:machine:mobile-base-1 s4-mobile-approach

zone_common=(--target "$zone" --target-name zone.ump.local \
  --target-machine ump:machine:transfer-zone-1)
ump --data-dir "$root/arm" reserve-resource "${zone_common[@]}" \
  --reservation-id s4-zone-reservation --task-id s4-shared-mission \
  --resource-id ump:resource:s4-transfer-zone --duration-ms 180000 \
  | tee -a "$trace"
ump --data-dir "$root/arm" propose-handoff "${zone_common[@]}" \
  --handoff-id s4-payload-handoff --task-id s4-shared-mission \
  --destination ump:machine:mobile-base-1 --subject-id ump:subject:s4-package \
  --reservation-id s4-zone-reservation --reference-frame world \
  --subject-frame package/body --x 0 --y 1.0 --z 0.65 \
  --maximum-age-ms 90000 --deadline-after-ms 180000 | tee -a "$trace"
ump --data-dir "$root/arm" update-handoff "${zone_common[@]}" \
  --handoff-id s4-payload-handoff --state prepared --previous-revision 1 \
  | tee -a "$trace"
ump --data-dir "$root/mobile" update-handoff "${zone_common[@]}" \
  --handoff-id s4-payload-handoff --state ready --previous-revision 2 \
  | tee -a "$trace"

issue "$arm" arm.ump.local ump:machine:robot-arm-1 lease-s4-arm \
  s4-arm-place org.ump.manipulation.arm_pose \
  '{"shoulder_rad":0.9,"elbow_rad":-1.2,"settle_seconds":1.5}'
wait_task "$arm" arm.ump.local ump:machine:robot-arm-1 s4-arm-place

ump --data-dir "$root/arm" update-handoff "${zone_common[@]}" \
  --handoff-id s4-payload-handoff --state transferring --previous-revision 3 \
  | tee -a "$trace"

issue "$arm" arm.ump.local ump:machine:robot-arm-1 lease-s4-arm \
  s4-arm-release org.ump.payload.detach '{}'
wait_task "$arm" arm.ump.local ump:machine:robot-arm-1 s4-arm-release
issue "$mobile" mobile.ump.local ump:machine:mobile-base-1 lease-s4-mobile \
  s4-mobile-receive org.ump.payload.attach '{}'
wait_task "$mobile" mobile.ump.local ump:machine:mobile-base-1 s4-mobile-receive

ump --data-dir "$root/arm" update-handoff "${zone_common[@]}" \
  --handoff-id s4-payload-handoff --state transferring --previous-revision 4 \
  --evidence-type physical_release_confirmed --evidence s4-arm-release \
  | tee -a "$trace"
ump --data-dir "$root/mobile" update-handoff "${zone_common[@]}" \
  --handoff-id s4-payload-handoff --state transferring --previous-revision 5 \
  --evidence-type payload_attached_confirmed --evidence s4-mobile-receive \
  | tee -a "$trace"
ump --data-dir "$root/mobile" update-handoff "${zone_common[@]}" \
  --handoff-id s4-payload-handoff --state committed --previous-revision 6 \
  | tee -a "$trace"
ump --data-dir "$root/mobile" query-handoff "${zone_common[@]}" \
  --handoff-id s4-payload-handoff | tee -a "$trace"
ump --data-dir "$root/arm" release-resource "${zone_common[@]}" \
  --reservation-id s4-zone-reservation --previous-revision 1 \
  --reason ownership_committed | tee -a "$trace"

issue "$mobile" mobile.ump.local ump:machine:mobile-base-1 lease-s4-mobile \
  s4-mobile-deliver org.ump.navigation.navigate \
  '{"target_x":3.0,"target_y":-1.0,"tolerance_m":0.2,"max_speed_mps":0.8}'
wait_task "$mobile" mobile.ump.local ump:machine:mobile-base-1 s4-mobile-deliver

python3 "$(dirname "$0")/verify_trace.py" "$trace" --report "$report"
"$(dirname "$0")/inspect.sh"

echo "S4 nominal mission completed; trace: $trace; invariant report: $report"
