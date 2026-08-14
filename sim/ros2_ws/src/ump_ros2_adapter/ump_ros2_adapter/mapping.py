"""Pure mapping helpers shared by the ROS node and unit tests."""

from __future__ import annotations

from dataclasses import dataclass
import math


OPERATIONAL_STARTING = 1
OPERATIONAL_IDLE = 2
OPERATIONAL_BUSY = 3
OPERATIONAL_DEGRADED = 5
OPERATIONAL_FAULTED = 7

SAFETY_NORMAL = 1
SAFETY_PROTECTIVE_STOP = 2
SAFETY_EMERGENCY_STOP = 3
SAFETY_RECOVERY_REQUIRED = 4
SAFETY_UNKNOWN = 5


@dataclass(frozen=True)
class MappedDiagnostics:
    operational: int
    safety: int
    health_codes: tuple[str, ...]


def map_diagnostics(statuses: list[tuple[int, str, str]]) -> MappedDiagnostics:
    """Map `(level, hardware_id, message)` entries to bounded UMP state."""
    if not statuses:
        return MappedDiagnostics(OPERATIONAL_DEGRADED, SAFETY_UNKNOWN, ("diagnostics.missing",))

    highest = max(level for level, _, _ in statuses)
    health = tuple(
        f"ros.{hardware_id or 'unknown'}.{message or 'unspecified'}"
        for level, hardware_id, message in statuses
        if level > 0
    )
    if highest >= 3:
        return MappedDiagnostics(OPERATIONAL_FAULTED, SAFETY_EMERGENCY_STOP, health)
    if highest >= 2:
        return MappedDiagnostics(OPERATIONAL_FAULTED, SAFETY_RECOVERY_REQUIRED, health)
    if highest == 1:
        return MappedDiagnostics(OPERATIONAL_DEGRADED, SAFETY_NORMAL, health)
    return MappedDiagnostics(OPERATIONAL_IDLE, SAFETY_NORMAL, ())


def deadline_expired(now_nanoseconds: int, deadline_sec: int, deadline_nanosec: int) -> bool:
    deadline = deadline_sec * 1_000_000_000 + deadline_nanosec
    return deadline > 0 and now_nanoseconds >= deadline


def validate_spatial_transform(
    now_nanoseconds: int,
    stamp_nanoseconds: int,
    translation: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
    previous_translation: tuple[float, float, float] | None,
    previous_rotation: tuple[float, float, float, float] | None,
    maximum_age_nanoseconds: int,
    maximum_translation_jump_m: float,
    maximum_rotation_jump_rad: float,
    position_uncertainty_m: float,
    orientation_uncertainty_rad: float,
    maximum_position_uncertainty_m: float,
    maximum_orientation_uncertainty_rad: float,
    uncertainty_stamp_nanoseconds: int | None = None,
    maximum_uncertainty_age_nanoseconds: int = 0,
) -> str:
    """Return an empty string for a usable transform, otherwise a stable reason code."""
    if not all(math.isfinite(value) for value in translation + rotation):
        return "tf.non_finite"
    norm = math.sqrt(sum(value * value for value in rotation))
    if abs(norm - 1.0) > 0.01:
        return "tf.invalid_quaternion"
    if uncertainty_stamp_nanoseconds is None:
        return "localization.unavailable"
    if uncertainty_stamp_nanoseconds > now_nanoseconds + 50_000_000:
        return "localization.timestamp_in_future"
    if (
        maximum_uncertainty_age_nanoseconds
        and now_nanoseconds - uncertainty_stamp_nanoseconds
        > maximum_uncertainty_age_nanoseconds
    ):
        return "localization.stale"
    if not math.isfinite(position_uncertainty_m) or position_uncertainty_m < 0.0:
        return "localization.invalid_position_uncertainty"
    if not math.isfinite(orientation_uncertainty_rad) or orientation_uncertainty_rad < 0.0:
        return "localization.invalid_orientation_uncertainty"
    if position_uncertainty_m > maximum_position_uncertainty_m:
        return "localization.position_uncertainty"
    if orientation_uncertainty_rad > maximum_orientation_uncertainty_rad:
        return "localization.orientation_uncertainty"
    # Static TF records have a zero timestamp and are timeless.
    if stamp_nanoseconds:
        if stamp_nanoseconds > now_nanoseconds + 50_000_000:
            return "tf.timestamp_in_future"
        if now_nanoseconds - stamp_nanoseconds > maximum_age_nanoseconds:
            return "tf.stale"
    if previous_translation is not None:
        jump = math.sqrt(
            sum(
                (current - previous) ** 2
                for current, previous in zip(translation, previous_translation)
            )
        )
        if jump > maximum_translation_jump_m:
            return "tf.translation_discontinuity"
    if previous_rotation is not None:
        dot = abs(
            sum(current * previous for current, previous in zip(rotation, previous_rotation))
        )
        angular_jump = 2.0 * math.acos(min(1.0, dot))
        if angular_jump > maximum_rotation_jump_rad:
            return "tf.rotation_discontinuity"
    return ""


def requires_safety_stop(safety: int) -> bool:
    return safety in {
        SAFETY_PROTECTIVE_STOP,
        SAFETY_EMERGENCY_STOP,
        SAFETY_RECOVERY_REQUIRED,
    }


def apply_safety_override(mapped: MappedDiagnostics, safety: int) -> MappedDiagnostics:
    if requires_safety_stop(safety):
        return MappedDiagnostics(
            OPERATIONAL_FAULTED,
            safety,
            mapped.health_codes + ("ros.safety.override",),
        )
    return MappedDiagnostics(mapped.operational, safety, mapped.health_codes)


def latch_safety_override(current: int | None, observed: int) -> int:
    """Latch a stop until a trusted local reset path is implemented."""
    if current is not None and requires_safety_stop(current):
        if observed == SAFETY_EMERGENCY_STOP:
            return SAFETY_EMERGENCY_STOP
        return current
    return observed
