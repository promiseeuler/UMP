#!/usr/bin/env bash
set -euo pipefail

: "${ROS_DISTRO:?Source ROS 2 before running the smoke test}"

pids=()
cleanup() {
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

share=$(ros2 pkg prefix ump_gazebo_demo --share)
gz sim -s -r -v 2 "$share/worlds/three_robot_world.sdf" > /tmp/ump-gazebo.log 2>&1 &
pids+=("$!")

start_server() {
  local namespace=$1 capability=$2 duration=$3
  ros2 run ump_gazebo_demo proxy_action_server --ros-args \
    -r "__ns:=/$namespace" -p "capability:=$capability" -p "duration_s:=$duration" \
    >"/tmp/${namespace}.log" 2>&1 &
  pids+=("$!")
}
start_server robot_quadruped_1 ump.navigation.inspect/v1 0.2
start_server robot_humanoid_1 ump.material.carry/v1 0.3
start_server robot_mobile_arm_1 ump.manipulation.place/v1 5.0

ready=false
for _ in $(seq 1 60); do
  actions=$(timeout 3s ros2 action list 2>/dev/null || true)
  services=$(timeout 3s gz service -l 2>/dev/null || true)
  if [[ $(grep -c execute_capability <<<"$actions" || true) -eq 3 ]] \
    && grep -q /world/ump_conformance/control <<<"$services"; then
    ready=true
    break
  fi
  sleep 1
done
if [[ $ready != true ]]; then
  cat /tmp/ump-gazebo.log >&2
  exit 1
fi

exercise() {
  ros2 run ump_gazebo_demo action_smoke_client "$@"
}
exercise --action /robot_quadruped_1/execute_capability \
  --assignment-id smoke-inspect --capability ump.navigation.inspect/v1 --expect succeeded
exercise --action /robot_humanoid_1/execute_capability \
  --assignment-id smoke-carry --capability ump.material.carry/v1 --expect succeeded
exercise --action /robot_mobile_arm_1/execute_capability \
  --assignment-id smoke-place --capability ump.manipulation.place/v1 --expect cancelled
exercise --action /robot_quadruped_1/execute_capability \
  --assignment-id smoke-reject --capability ump.material.carry/v1 --expect rejected

printf '%s\n' 'UMP ROS 2 / Gazebo smoke test passed'
