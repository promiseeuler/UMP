"""Deterministic command parsing and control helpers for the S4 fixture."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class NavigationCommand:
    target_x: float
    target_y: float
    tolerance_m: float
    max_speed_mps: float


@dataclass(frozen=True)
class ArmCommand:
    shoulder_rad: float
    elbow_rad: float
    settle_seconds: float


@dataclass(frozen=True)
class DriveCommand:
    linear_x: float
    angular_z: float
    distance_m: float
    reached: bool


@dataclass
class ProgressWatchdog:
    timeout_seconds: float
    minimum_progress_m: float
    best_distance_m: float | None = None
    last_progress_time: float | None = None

    def observe(self, distance_m: float, now_seconds: float) -> bool:
        """Return true after sustained lack of translational progress."""
        if self.best_distance_m is None or self.last_progress_time is None:
            self.best_distance_m = distance_m
            self.last_progress_time = now_seconds
            return False
        if distance_m <= self.best_distance_m - self.minimum_progress_m:
            self.best_distance_m = distance_m
            self.last_progress_time = now_seconds
            return False
        return now_seconds - self.last_progress_time >= self.timeout_seconds


def parse_navigation(payload: bytes) -> NavigationCommand:
    value = json.loads(payload or b"{}")
    target_x = _finite(value["target_x"], "target_x")
    target_y = _finite(value.get("target_y", 0.0), "target_y")
    tolerance = max(0.02, _finite(value.get("tolerance_m", 0.08), "tolerance_m"))
    speed = min(0.8, max(0.05, _finite(value.get("max_speed_mps", 0.4), "max_speed_mps")))
    return NavigationCommand(target_x, target_y, tolerance, speed)


def parse_arm(payload: bytes) -> ArmCommand:
    value = json.loads(payload or b"{}")
    shoulder = _finite(value["shoulder_rad"], "shoulder_rad")
    elbow = _finite(value["elbow_rad"], "elbow_rad")
    if not -1.4 <= shoulder <= 1.4 or not -1.8 <= elbow <= 1.8:
        raise ValueError("arm command exceeds simulated joint limits")
    settle = min(8.0, max(0.1, _finite(value.get("settle_seconds", 1.5), "settle_seconds")))
    return ArmCommand(shoulder, elbow, settle)


def compute_drive(command: NavigationCommand, x: float, y: float, yaw: float) -> DriveCommand:
    dx, dy = command.target_x - x, command.target_y - y
    distance = math.hypot(dx, dy)
    if distance <= command.tolerance_m:
        return DriveCommand(0.0, 0.0, distance, True)
    desired_yaw = math.atan2(dy, dx)
    yaw_error = math.atan2(math.sin(desired_yaw - yaw), math.cos(desired_yaw - yaw))
    angular = max(-1.2, min(1.2, 2.0 * yaw_error))
    linear = min(command.max_speed_mps, distance) if abs(yaw_error) < 0.45 else 0.0
    return DriveCommand(linear, angular, distance, False)


def _finite(value, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number
