import math

import pytest

from ump_ros2_adapter.control import (
    ProgressWatchdog,
    compute_drive,
    parse_arm,
    parse_navigation,
)


def test_navigation_parser_bounds_speed_and_rejects_non_finite_values():
    command = parse_navigation(b'{"target_x":2,"max_speed_mps":99}')
    assert command.max_speed_mps == 0.8
    with pytest.raises(ValueError):
        parse_navigation(b'{"target_x":NaN}')


def test_navigation_parser_accepts_the_exact_s4_mission_contract():
    command = parse_navigation(
        b'{"target_x":0.0,"target_y":1.0,"tolerance_m":0.18,"max_speed_mps":0.7}'
    )
    assert command.target_y == 1.0
    assert command.tolerance_m == 0.18
    assert command.max_speed_mps == 0.7


def test_drive_rotates_before_advancing_and_stops_in_tolerance():
    command = parse_navigation(b'{"target_x":1,"target_y":0,"tolerance_m":0.1}')
    turning = compute_drive(command, 0.0, 0.0, math.pi)
    assert turning.linear_x == 0.0
    assert abs(turning.angular_z) == 1.2
    reached = compute_drive(command, 0.95, 0.0, 0.0)
    assert reached.reached
    assert reached.linear_x == 0.0


def test_drive_speed_is_bounded():
    command = parse_navigation(b'{"target_x":20,"max_speed_mps":0.3}')
    assert compute_drive(command, 0.0, 0.0, 0.0).linear_x == 0.3


def test_arm_parser_enforces_physical_joint_limits():
    command = parse_arm(b'{"shoulder_rad":1.0,"elbow_rad":-1.2}')
    assert command.elbow_rad == -1.2
    with pytest.raises(ValueError):
        parse_arm(b'{"shoulder_rad":2.0,"elbow_rad":0}')


def test_progress_watchdog_requires_sustained_stall_and_resets_on_progress():
    watchdog = ProgressWatchdog(timeout_seconds=4.0, minimum_progress_m=0.03)
    assert not watchdog.observe(3.0, 0.0)
    assert not watchdog.observe(2.98, 3.9)
    assert watchdog.observe(2.98, 4.0)

    watchdog = ProgressWatchdog(timeout_seconds=4.0, minimum_progress_m=0.03)
    assert not watchdog.observe(3.0, 0.0)
    assert not watchdog.observe(2.95, 3.0)
    assert not watchdog.observe(2.95, 6.9)
    assert watchdog.observe(2.95, 7.0)
