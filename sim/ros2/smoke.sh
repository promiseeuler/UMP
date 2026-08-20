#!/usr/bin/env bash
set -eo pipefail

world=/workspaces/ump_ros2/install/share/ump_gazebo/worlds/s4_warehouse.sdf
models=/workspaces/ump_ros2/install/share/ump_gazebo/models
variant=${UMP_MOBILE_MODEL_VARIANT:-reference}
case "$variant" in
  reference) export GZ_SIM_RESOURCE_PATH="$models" ;;
  alternate) export GZ_SIM_RESOURCE_PATH="$models/alternate:$models" ;;
  *) echo "unsupported UMP_MOBILE_MODEL_VARIANT: $variant" >&2; exit 2 ;;
esac
log=/tmp/ump-s4-gazebo.log
gz sim -r -s "$world" >"$log" 2>&1 &
server_pid=$!

cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 60); do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    cat "$log"
    exit 1
  fi
  if gz service -l 2>/dev/null | grep -q '^/world/s4/control$'; then
    break
  fi
  sleep 0.25
done

services=$(gz service -l)
models=$(gz model --list)
grep -q '^/world/s4/control$' <<<"$services"
for model in floor transfer_zone mobile_base robot_arm package; do
  grep -q "^- $model$" <<<"$models"
done

state_log=/tmp/ump-payload-state.log
wait_for_state() {
  local pid=$1
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid"
      return
    fi
    sleep 0.1
  done
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  cat "$log"
  echo "timed out waiting for payload state" >&2
  exit 1
}

gz topic -t /ump/payload/fail_next_attach -m gz.msgs.Boolean -p 'data: true'
gz topic -e -t /ump/payload/state -n 1 >"$state_log" &
state_pid=$!
sleep 0.2
gz topic -t /ump/payload/attach_arm -m gz.msgs.Boolean -p 'data: true'
wait_for_state "$state_pid"
grep -q 'rejected:grasp_failed' "$state_log"

gz topic -e -t /ump/payload/state -n 1 >"$state_log" &
state_pid=$!
sleep 0.2
gz topic -t /ump/payload/drop -m gz.msgs.Boolean -p 'data: true'
wait_for_state "$state_pid"
grep -q 'dropped' "$state_log"

gz topic -e -t /ump/payload/state -n 1 >"$state_log" &
state_pid=$!
sleep 0.2
gz topic -t /ump/payload/attach_mobile -m gz.msgs.Boolean -p 'data: true'
wait_for_state "$state_pid"
grep -q 'rejected:out_of_range' "$state_log"

gz topic -e -t /ump/payload/state -n 1 >"$state_log" &
state_pid=$!
sleep 0.2
gz topic -t /ump/payload/detach -m gz.msgs.Boolean -p 'data: true'
wait_for_state "$state_pid"
grep -q 'unowned' "$state_log"

echo "S4 Gazebo smoke passed for $variant mobile model: entities and payload rules respond."
