#!/usr/bin/env bash
set -euo pipefail

root=${UMP_S4_ROOT:-/run/ump-faults}
log_dir=${UMP_S4_LOG_DIR:-/tmp/ump-s4-faults}
export UMP_S4_ROOT="$root"
mkdir -p "$log_dir"
/workspaces/ump_ros2/s4/setup.sh >/dev/null
UMP_DATA_DIR="$root/mobile" umpd >"$log_dir/mobile-umpd.log" 2>&1 & mobile_pid=$!
UMP_DATA_DIR="$root/arm" umpd >"$log_dir/arm-umpd.log" 2>&1 & arm_pid=$!
UMP_DATA_DIR="$root/zone" umpd >"$log_dir/zone-umpd.log" 2>&1 & zone_pid=$!
ros2 launch ump_gazebo s4_stack.launch.py >"$log_dir/ros.log" 2>&1 & ros_pid=$!
cleanup() {
  kill "$ros_pid" "$mobile_pid" "$arm_pid" "$zone_pid" 2>/dev/null || true
  wait "$ros_pid" "$mobile_pid" "$arm_pid" "$zone_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 100); do
  test -S "$root/mobile/adapter.sock" && break
  sleep 0.1
done
test -S "$root/mobile/adapter.sock"
for _ in $(seq 1 100); do
  test -S "$root/arm/adapter.sock" && break
  sleep 0.1
done
test -S "$root/arm/adapter.sock"
for _ in $(seq 1 100); do
  test -S "$root/zone/adapter.sock" && break
  sleep 0.1
done
test -S "$root/zone/adapter.sock"
for _ in $(seq 1 100); do
  actions=$(ros2 action list 2>/dev/null || true)
  if grep -q '^/mobile_controller/execute_capability$' <<<"$actions" \
    && grep -q '^/arm_controller/execute_capability$' <<<"$actions"; then
    break
  fi
  sleep 0.1
done
ros2 action list | grep -q '^/mobile_controller/execute_capability$'
ros2 action list | grep -q '^/arm_controller/execute_capability$'
sleep 4

zone_common=(--target 127.0.0.1:17443 --target-name zone.ump.local \
  --target-machine ump:machine:transfer-zone-1)
arm_common=(--target 127.0.0.1:17442 --target-name arm.ump.local \
  --target-machine ump:machine:robot-arm-1)
mobile_common=(--target 127.0.0.1:17441 --target-name mobile.ump.local \
  --target-machine ump:machine:mobile-base-1)

ros2 topic pub --once /ump/payload/fail_next_attach std_msgs/msg/Bool \
  "{data: true}" >/dev/null
set +e
ump --data-dir "$root/coordinator" issue-task "${arm_common[@]}" \
  --task-id s4-failed-grasp --capability org.ump.payload.attach \
  --input-json '{}' --lease-id lease-s4-arm --idempotency-key s4-failed-grasp \
  --deadline-after-ms 10000 --wait >"$log_dir/failed-grasp-task.txt" 2>&1
grasp_status=$?
set -e
test "$grasp_status" -ne 0
grasp_task=$(ump --data-dir "$root/coordinator" query-task "${arm_common[@]}" \
  --task-id s4-failed-grasp)
case "$grasp_task" in *'"status":"failed"'*) ;; *) echo "$grasp_task" >&2; exit 1 ;; esac
ump --data-dir "$root/arm" timeline s4-failed-grasp >"$log_dir/failed-grasp-timeline.json"
grep -q 'ros.payload_rejected:grasp_failed' "$log_dir/failed-grasp-timeline.json"

ump --data-dir "$root/coordinator" issue-task "${arm_common[@]}" \
  --task-id s4-grasp-recovery --capability org.ump.payload.attach \
  --input-json '{}' --lease-id lease-s4-arm --idempotency-key s4-grasp-recovery \
  --deadline-after-ms 10000 --wait >"$log_dir/grasp-recovery-task.txt"

ump --data-dir "$root/arm" reserve-resource "${zone_common[@]}" \
  --reservation-id s4-drop-zone --task-id s4-drop-handoff \
  --resource-id ump:resource:s4-transfer-zone --duration-ms 60000 >/dev/null
ump --data-dir "$root/arm" propose-handoff "${zone_common[@]}" \
  --handoff-id s4-drop-handoff --task-id s4-drop-handoff \
  --destination ump:machine:mobile-base-1 --subject-id ump:subject:s4-package \
  --reservation-id s4-drop-zone --reference-frame world \
  --subject-frame package/body --x 0 --y 1 --z 0.65 \
  --maximum-age-ms 10000 --deadline-after-ms 30000 >/dev/null
ump --data-dir "$root/arm" update-handoff "${zone_common[@]}" \
  --handoff-id s4-drop-handoff --state prepared --previous-revision 1 >/dev/null
ump --data-dir "$root/mobile" update-handoff "${zone_common[@]}" \
  --handoff-id s4-drop-handoff --state ready --previous-revision 2 >/dev/null
ump --data-dir "$root/arm" update-handoff "${zone_common[@]}" \
  --handoff-id s4-drop-handoff --state transferring --previous-revision 3 >/dev/null
ros2 topic pub --once /ump/payload/drop std_msgs/msg/Bool "{data: true}" >/dev/null
drop_status=""
for _ in $(seq 1 50); do
  drop_status=$(ump --data-dir "$root/arm" query-handoff "${zone_common[@]}" \
    --handoff-id s4-drop-handoff 2>/dev/null || true)
  case "$drop_status" in *'"state":"unknown"'*) break ;; esac
  sleep 0.1
done
case "$drop_status" in *'"state":"unknown"'*) ;; *) echo "$drop_status" >&2; exit 1 ;; esac
case "$drop_status" in *'"inspection_required":true'*) ;;
  *) echo "$drop_status" >&2; exit 1 ;;
esac
case "$drop_status" in *'"authoritative_owner_machine_id":"ump:machine:robot-arm-1"'*) ;;
  *) echo "$drop_status" >&2; exit 1 ;;
esac
ump --data-dir "$root/arm" release-resource "${zone_common[@]}" \
  --reservation-id s4-drop-zone --previous-revision 1 --reason drop_isolated >/dev/null

ump --data-dir "$root/arm" reserve-resource "${zone_common[@]}" \
  --reservation-id s4-physical-intrusion-zone --task-id s4-physical-intrusion \
  --resource-id ump:resource:s4-transfer-zone --duration-ms 60000 >/dev/null
ump --data-dir "$root/arm" propose-handoff "${zone_common[@]}" \
  --handoff-id s4-physical-intrusion --task-id s4-physical-intrusion \
  --destination ump:machine:mobile-base-1 --subject-id ump:subject:s4-package \
  --reservation-id s4-physical-intrusion-zone --reference-frame world \
  --subject-frame package/body --x 0 --y 1 --z 0.65 \
  --maximum-age-ms 10000 --deadline-after-ms 30000 >/dev/null
ump --data-dir "$root/arm" update-handoff "${zone_common[@]}" \
  --handoff-id s4-physical-intrusion --state prepared --previous-revision 1 >/dev/null
ump --data-dir "$root/mobile" update-handoff "${zone_common[@]}" \
  --handoff-id s4-physical-intrusion --state ready --previous-revision 2 >/dev/null
ump --data-dir "$root/arm" update-handoff "${zone_common[@]}" \
  --handoff-id s4-physical-intrusion --state transferring --previous-revision 3 >/dev/null

intruder_sdf=$(tr -d '\n' </workspaces/ump_ros2/s4/zone_intruder.sdf)
gz service -s /world/s4/create --reqtype gz.msgs.EntityFactory \
  --reptype gz.msgs.Boolean --timeout 5000 --req "sdf: \"$intruder_sdf\"" \
  >"$log_dir/create-intruder.txt"
intrusion_status=""
for _ in $(seq 1 50); do
  intrusion_status=$(ump --data-dir "$root/arm" query-handoff "${zone_common[@]}" \
    --handoff-id s4-physical-intrusion 2>/dev/null || true)
  case "$intrusion_status" in *'"state":"unknown"'*) break ;; esac
  sleep 0.1
done
case "$intrusion_status" in *'"state":"unknown"'*) ;; *) echo "$intrusion_status" >&2; exit 1 ;; esac
case "$intrusion_status" in *'"inspection_required":true'*) ;;
  *) echo "$intrusion_status" >&2; exit 1 ;;
esac
case "$intrusion_status" in *'"authoritative_owner_machine_id":"ump:machine:robot-arm-1"'*) ;;
  *) echo "$intrusion_status" >&2; exit 1 ;;
esac

ros2 topic pub --once /ump/fault/mobile_tf std_msgs/msg/String "{data: stale}" >/dev/null
spatial="$log_dir/stale-spatial.txt"
for _ in $(seq 1 20); do
  timeout 3 ros2 topic echo /mobile/ump_ros2_adapter/spatial --once >"$spatial" 2>/dev/null || true
  grep -q 'invalid_reason: tf.stale' "$spatial" && break
done
grep -q 'valid: false' "$spatial"
grep -q 'invalid_reason: tf.stale' "$spatial"
ros2 topic pub --once /ump/fault/mobile_tf std_msgs/msg/String "{data: normal}" >/dev/null

ros2 topic pub --once /ump/fault/mobile_tf std_msgs/msg/String "{data: jump}" >/dev/null
jump_spatial="$log_dir/jump-spatial.txt"
for _ in $(seq 1 20); do
  timeout 3 ros2 topic echo /mobile/ump_ros2_adapter/spatial --once \
    >"$jump_spatial" 2>/dev/null || true
  grep -q 'invalid_reason: tf.translation_discontinuity' "$jump_spatial" && break
done
grep -q 'valid: false' "$jump_spatial"
grep -q 'invalid_reason: tf.translation_discontinuity' "$jump_spatial"
ros2 topic pub --once /ump/fault/mobile_tf std_msgs/msg/String "{data: normal}" >/dev/null
for _ in $(seq 1 20); do
  timeout 3 ros2 topic echo /mobile/ump_ros2_adapter/spatial --once \
    >"$jump_spatial" 2>/dev/null || true
  grep -q 'valid: true' "$jump_spatial" && break
done
grep -q 'valid: true' "$jump_spatial"

ros2 topic pub --once /ump/fault/mobile_tf std_msgs/msg/String "{data: uncertain}" >/dev/null
uncertain_spatial="$log_dir/uncertain-spatial.txt"
for _ in $(seq 1 20); do
  timeout 3 ros2 topic echo /mobile/ump_ros2_adapter/spatial --once \
    >"$uncertain_spatial" 2>/dev/null || true
  grep -q 'invalid_reason: localization.position_uncertainty' "$uncertain_spatial" && break
done
grep -q 'valid: false' "$uncertain_spatial"
grep -q 'position_uncertainty_m: 2.0' "$uncertain_spatial"
grep -q 'invalid_reason: localization.position_uncertainty' "$uncertain_spatial"
ros2 topic pub --once /ump/fault/mobile_tf std_msgs/msg/String "{data: normal}" >/dev/null
for _ in $(seq 1 20); do
  timeout 3 ros2 topic echo /mobile/ump_ros2_adapter/spatial --once \
    >"$uncertain_spatial" 2>/dev/null || true
  grep -q 'valid: true' "$uncertain_spatial" && break
done
grep -q 'valid: true' "$uncertain_spatial"
grep -q 'position_uncertainty_m: 0.02' "$uncertain_spatial"

obstacle_sdf=$(tr -d '\n' </workspaces/ump_ros2/s4/blocked_path_obstacle.sdf)
gz service -s /world/s4/create --reqtype gz.msgs.EntityFactory \
  --reptype gz.msgs.Boolean --timeout 5000 --req "sdf: \"$obstacle_sdf\"" \
  >"$log_dir/create-obstacle.txt"
for _ in $(seq 1 40); do
  gz model --list 2>/dev/null | grep -q 'blocked_path_obstacle' && break
  sleep 0.1
done
gz model --list | grep -q 'blocked_path_obstacle'

set +e
ump --data-dir "$root/coordinator" issue-task \
  --target 127.0.0.1:17441 --target-name mobile.ump.local \
  --target-machine ump:machine:mobile-base-1 \
  --task-id s4-blocked-navigation --capability org.ump.navigation.navigate \
  --input-json '{"target_x":0.0,"target_y":0.0,"tolerance_m":0.1,"max_speed_mps":0.5}' \
  --lease-id lease-s4-mobile --idempotency-key s4-blocked-navigation \
  --deadline-after-ms 30000 --wait >"$log_dir/blocked-task.txt" 2>&1
issue_status=$?
set -e
test "$issue_status" -ne 0
task=$(ump --data-dir "$root/coordinator" query-task \
  --target 127.0.0.1:17441 --target-name mobile.ump.local \
  --target-machine ump:machine:mobile-base-1 --task-id s4-blocked-navigation)
case "$task" in *'"status":"failed"'*) ;; *) echo "$task" >&2; exit 1 ;; esac
ump --data-dir "$root/mobile" timeline s4-blocked-navigation \
  >"$log_dir/blocked-timeline.json"
grep -q 'ros.navigation_blocked' "$log_dir/blocked-timeline.json"

ump --data-dir "$root/coordinator" issue-task "${mobile_common[@]}" \
  --task-id s4-protective-stop --capability org.ump.navigation.navigate \
  --input-json '{"target_x":3.0,"target_y":-1.0,"tolerance_m":0.1,"max_speed_mps":0.5}' \
  --lease-id lease-s4-mobile --idempotency-key s4-protective-stop \
  --deadline-after-ms 30000 >"$log_dir/protective-stop-issue.txt"
for _ in $(seq 1 50); do
  safety_task=$(ump --data-dir "$root/coordinator" query-task "${mobile_common[@]}" \
    --task-id s4-protective-stop)
  case "$safety_task" in *'"status":"running"'*) break ;; esac
  sleep 0.1
done
case "$safety_task" in *'"status":"running"'*) ;; *) echo "$safety_task" >&2; exit 1 ;; esac
ros2 topic pub --once /mobile/ump_ros2_adapter/safety ump_interfaces/msg/SafetyEvent \
  "{state: 2, source: local_safety_controller, code: protective_stop}" >/dev/null
for _ in $(seq 1 50); do
  safety_task=$(ump --data-dir "$root/coordinator" query-task "${mobile_common[@]}" \
    --task-id s4-protective-stop)
  case "$safety_task" in *'"status":"cancelled"'*) break ;; esac
  sleep 0.1
done
case "$safety_task" in *'"status":"cancelled"'*) ;; *) echo "$safety_task" >&2; exit 1 ;; esac
ros2 topic pub --once /mobile/ump_ros2_adapter/safety ump_interfaces/msg/SafetyEvent \
  "{state: 3, source: local_safety_controller, code: emergency_stop}" >/dev/null
ros2 topic pub --once /mobile/ump_ros2_adapter/safety ump_interfaces/msg/SafetyEvent \
  "{state: 1, source: remote_request, code: reset_attempt}" >/dev/null
sleep 0.5
grep -q '"safety":"emergency_stop"' "$root/mobile/runtime-state.json"
ump --data-dir "$root/coordinator" issue-task "${mobile_common[@]}" \
  --task-id s4-post-emergency-gate --capability org.ump.navigation.navigate \
  --input-json '{"target_x":0.0,"target_y":0.0}' --lease-id lease-s4-mobile \
  --idempotency-key s4-post-emergency-gate --deadline-after-ms 30000 >/dev/null
sleep 0.5
post_emergency=$(ump --data-dir "$root/coordinator" query-task "${mobile_common[@]}" \
  --task-id s4-post-emergency-gate)
case "$post_emergency" in *'"status":"accepted"'*) ;;
  *) echo "$post_emergency" >&2; exit 1 ;;
esac

UMP_S4_INSPECTOR_PROFILE=fault /workspaces/ump_ros2/s4/inspect.sh

echo "S4 embodied faults passed: payload, zone, TF, localization, navigation, and safety faults converged safely."
