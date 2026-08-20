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

diagnostics() {
  for log in /tmp/ump-gazebo.log /tmp/robot_*.log; do
    if [[ -f $log ]]; then
      printf '%s\n' "--- $log ---" >&2
      cat "$log" >&2
    fi
  done
}

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
start_server robot_quadruped_1 ump.navigation.inspect-route/v1 0.2
start_server robot_humanoid_1 ump.material.carry/v1 0.3
start_server robot_mobile_arm_1 ump.manipulation.place/v1 5.0

ready=false
for _ in $(seq 1 45); do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      diagnostics
      exit 1
    fi
  done
  actions=$(timeout 1s ros2 action list 2>/dev/null || true)
  services=$(timeout 1s gz service -l 2>/dev/null || true)
  if [[ $(grep -c execute_capability <<<"$actions" || true) -eq 3 ]] \
    && grep -q /world/ump_conformance/control <<<"$services"; then
    ready=true
    break
  fi
  sleep 1
done
if [[ $ready != true ]]; then
  diagnostics
  exit 1
fi

exercise() {
  ros2 run ump_gazebo_demo action_smoke_client "$@"
}
inspect_result=$(exercise --action /robot_quadruped_1/execute_capability \
  --assignment-id smoke-inspect --capability ump.navigation.inspect-route/v1 --expect succeeded
)
adapter_result=$(ros2 run ump_gazebo_demo adapter_smoke_client \
  --action /robot_humanoid_1/execute_capability \
  --robot-id robot-humanoid-1 \
  --assignment-id smoke-adapter-carry \
  --capability ump.material.carry/v1 \
  --inputs-json '{"object":"package-1","destination":"storage"}'
)
carry_result=$(exercise --action /robot_humanoid_1/execute_capability \
  --assignment-id smoke-carry --capability ump.material.carry/v1 --expect succeeded
)
cancel_result=$(exercise --action /robot_mobile_arm_1/execute_capability \
  --assignment-id smoke-place --capability ump.manipulation.place/v1 --expect cancelled
)
reject_result=$(exercise --action /robot_quadruped_1/execute_capability \
  --assignment-id smoke-reject --capability ump.material.carry/v1 --expect rejected
)

export UMP_SMOKE_INSPECT_RESULT="$inspect_result"
export UMP_SMOKE_ADAPTER_RESULT="$adapter_result"
export UMP_SMOKE_CARRY_RESULT="$carry_result"
export UMP_SMOKE_CANCEL_RESULT="$cancel_result"
export UMP_SMOKE_REJECT_RESULT="$reject_result"
export UMP_SMOKE_WORLD="$share/worlds/three_robot_world.sdf"
export UMP_SMOKE_REPORT="${UMP_SMOKE_REPORT:-/tmp/ump-ros2-gazebo-smoke.json}"
python3 - <<'PY'
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import socket

result_names = (
    "INSPECT_RESULT",
    "ADAPTER_RESULT",
    "CARRY_RESULT",
    "CANCEL_RESULT",
    "REJECT_RESULT",
)
results = [json.loads(os.environ[f"UMP_SMOKE_{name}"]) for name in result_names]
world = Path(os.environ["UMP_SMOKE_WORLD"])
report = {
    "profile": "ump.ros2-gazebo-smoke/v1",
    "passed": True,
    "world": {
        "path": str(world),
        "sha256": sha256(world.read_bytes()).hexdigest(),
        "service": "/world/ump_conformance/control",
    },
    "checks": {
        "gazebo_world_ready": True,
        "three_action_servers_ready": True,
        "native_action_success": True,
        "ump_adapter_success": True,
        "native_cancellation": True,
        "capability_rejection": True,
    },
    "results": results,
    "environment": {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "ros_distro": os.environ["ROS_DISTRO"],
        "repository_revision": os.environ.get("GITHUB_SHA", "unknown"),
    },
}
encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
path = Path(os.environ["UMP_SMOKE_REPORT"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(encoded + "\n", encoding="utf-8")
print(encoded)
PY
