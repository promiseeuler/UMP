#!/usr/bin/env python3
"""Verify mixed-installation S5 deployment equivalence and proxy visibility."""

import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct-report", required=True)
    parser.add_argument("--gateway-report", required=True)
    parser.add_argument("--direct-ping", required=True)
    parser.add_argument("--gateway-ping", required=True)
    parser.add_argument("--gateway-status", required=True)
    parser.add_argument("--gateway-inspector", required=True)
    parser.add_argument("--fault-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ros-evidence")
    args = parser.parse_args()

    direct = load(args.direct_report)
    gateway = load(args.gateway_report)
    direct_ping = load(args.direct_ping)
    gateway_ping = load(args.gateway_ping)
    status = load(args.gateway_status)
    inspector = load(args.gateway_inspector)
    faults = load(args.fault_report)
    checks = {
        "direct_mission_passed": direct.get("passed") is True,
        "gateway_mission_passed": gateway.get("passed") is True,
        "same_task_count": direct.get("task_count") == gateway.get("task_count") == 6,
        "same_committed_owner": direct.get("final_owner") == gateway.get("final_owner"),
        "direct_is_not_proxy": direct_ping.get("proxy") is None,
        "gateway_declares_proxy": (
            gateway_ping.get("deployment_mode") == "DEPLOYMENT_MODE_GATEWAY_PROXY"
            and gateway_ping.get("proxy", {}).get("gateway_id") == "ump:gateway:s5-arm-01"
            and gateway_ping.get("proxy", {}).get("represented_machine_id")
            == "ump:machine:robot-arm-1"
        ),
        "operator_inspector_declares_proxy": (
            inspector.get("runtime", {}).get("deployment_mode") == "gateway_proxy"
            and inspector.get("runtime", {}).get("proxy", {}).get("gateway_id")
            == "ump:gateway:s5-arm-01"
        ),
        "operator_status_unambiguous": (
            status.get("proxy") is True
            and status.get("connectivity") == "connected"
            and status.get("mode") == "command"
            and status.get("fault") is None
        ),
        "controller_disconnect_observed": faults.get("controller_disconnect_observed") is True,
        "controller_recovery_observed": faults.get("controller_recovery_observed") is True,
        "gateway_restart_advanced_revision": faults.get("gateway_restart_advanced_revision") is True,
    }
    zone_form = "observer_runtime; ROS 2 package exercised by native S4 gate"
    if args.ros_evidence:
        ros = load(args.ros_evidence)
        checks.update({
            "installed_ros_package_prefix": ros.get("package_prefix") == "/opt/ump/ros/jazzy",
            "ros_adapter_direct_state_published": int(ros.get("direct_state_revision", 0)) >= 2,
            "ros_adapter_gateway_state_published": int(ros.get("gateway_state_revision", 0)) >= 2,
        })
        zone_form = "installed_ros2_jazzy_package"
    result = {
        "scenario": "S5 mixed installation protocol checkpoint",
        "passed": all(checks.values()),
        "checks": checks,
        "deployment_forms": {
            "mobile": "native_runtime",
            "arm_direct": "sdk_http_controller_bridge",
            "arm_fallback": "external_gateway",
            "zone": zone_form,
        },
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not result["passed"]:
        raise SystemExit(f"S5 verification failed: {[name for name, ok in checks.items() if not ok]}")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
