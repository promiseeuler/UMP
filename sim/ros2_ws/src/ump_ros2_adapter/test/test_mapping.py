from ump_ros2_adapter.mapping import (
    OPERATIONAL_DEGRADED,
    OPERATIONAL_FAULTED,
    OPERATIONAL_IDLE,
    SAFETY_EMERGENCY_STOP,
    SAFETY_NORMAL,
    SAFETY_UNKNOWN,
    apply_safety_override,
    deadline_expired,
    latch_safety_override,
    map_diagnostics,
    requires_safety_stop,
    validate_spatial_transform,
)


def test_diagnostics_nominal_and_emergency():
    assert map_diagnostics([(0, "drive", "ok")]).operational == OPERATIONAL_IDLE
    emergency = map_diagnostics([(3, "estop", "pressed")])
    assert emergency.operational == OPERATIONAL_FAULTED
    assert emergency.safety == SAFETY_EMERGENCY_STOP


def test_missing_diagnostics_is_unknown_and_degraded():
    state = map_diagnostics([])
    assert state.operational == OPERATIONAL_DEGRADED
    assert state.safety == SAFETY_UNKNOWN


def test_warning_is_not_a_safety_stop():
    state = map_diagnostics([(1, "battery", "low")])
    assert state.operational == OPERATIONAL_DEGRADED
    assert state.safety == SAFETY_NORMAL


def test_deadline_uses_ros_clock_nanoseconds():
    assert not deadline_expired(10, 0, 0)
    assert deadline_expired(2_000_000_000, 1, 999_999_999)


def test_protective_emergency_and_recovery_states_require_stop():
    assert requires_safety_stop(2)
    assert requires_safety_stop(3)
    assert requires_safety_stop(4)
    assert not requires_safety_stop(SAFETY_NORMAL)
    assert not requires_safety_stop(SAFETY_UNKNOWN)
    emergency = apply_safety_override(map_diagnostics([]), SAFETY_EMERGENCY_STOP)
    assert emergency.operational == OPERATIONAL_FAULTED
    assert emergency.safety == SAFETY_EMERGENCY_STOP


def test_safety_stop_latches_and_allows_escalation_but_not_remote_reset():
    assert latch_safety_override(None, SAFETY_NORMAL) == SAFETY_NORMAL
    assert latch_safety_override(SAFETY_NORMAL, 2) == 2
    assert latch_safety_override(2, SAFETY_NORMAL) == 2
    assert latch_safety_override(2, SAFETY_EMERGENCY_STOP) == SAFETY_EMERGENCY_STOP
    assert latch_safety_override(SAFETY_EMERGENCY_STOP, 2) == SAFETY_EMERGENCY_STOP
    assert latch_safety_override(SAFETY_EMERGENCY_STOP, SAFETY_UNKNOWN) == SAFETY_EMERGENCY_STOP
    assert (
        latch_safety_override(SAFETY_EMERGENCY_STOP, SAFETY_NORMAL)
        == SAFETY_EMERGENCY_STOP
    )


def test_spatial_validation_rejects_stale_future_and_malformed_transforms():
    args = (
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
        None,
        None,
        500_000_000,
        0.75,
        1.2,
        0.02,
        0.03,
        0.25,
        0.35,
        2_000_000_000,
        500_000_000,
    )
    assert validate_spatial_transform(2_000_000_000, 1_000_000_000, *args) == "tf.stale"
    assert (
        validate_spatial_transform(2_000_000_000, 2_100_000_000, *args)
        == "tf.timestamp_in_future"
    )
    malformed = (
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 0.0),
        None,
        None,
        500_000_000,
        0.75,
        1.2,
        0.02,
        0.03,
        0.25,
        0.35,
        2_000_000_000,
        500_000_000,
    )
    assert (
        validate_spatial_transform(2_000_000_000, 2_000_000_000, *malformed)
        == "tf.invalid_quaternion"
    )


def test_spatial_validation_detects_translation_and_rotation_discontinuities():
    assert (
        validate_spatial_transform(
            2_000_000_000,
            2_000_000_000,
            (1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
            500_000_000,
            0.75,
            1.2,
            0.02,
            0.03,
            0.25,
            0.35,
            2_000_000_000,
            500_000_000,
        )
        == "tf.translation_discontinuity"
    )
    assert (
        validate_spatial_transform(
            2_000_000_000,
            2_000_000_000,
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
            500_000_000,
            0.75,
            1.2,
            0.02,
            0.03,
            0.25,
            0.35,
            2_000_000_000,
            500_000_000,
        )
        == "tf.rotation_discontinuity"
    )


def test_static_and_small_transform_updates_are_valid():
    assert (
        validate_spatial_transform(
            2_000_000_000,
            0,
            (0.1, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
            500_000_000,
            0.75,
            1.2,
            0.02,
            0.03,
            0.25,
            0.35,
            2_000_000_000,
            500_000_000,
        )
        == ""
    )


def test_spatial_validation_enforces_declared_localization_uncertainty():
    common = (
        2_000_000_000,
        2_000_000_000,
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
        None,
        None,
        500_000_000,
        0.75,
        1.2,
    )
    assert (
        validate_spatial_transform(
            *common, 0.5, 0.03, 0.25, 0.35, 2_000_000_000, 500_000_000
        )
        == "localization.position_uncertainty"
    )
    assert (
        validate_spatial_transform(
            *common, 0.02, 0.5, 0.25, 0.35, 2_000_000_000, 500_000_000
        )
        == "localization.orientation_uncertainty"
    )
    assert (
        validate_spatial_transform(
            *common,
            float("nan"),
            0.03,
            0.25,
            0.35,
            2_000_000_000,
            500_000_000,
        )
        == "localization.invalid_position_uncertainty"
    )
    assert (
        validate_spatial_transform(
            *common, 0.02, 0.03, 0.25, 0.35, None, 500_000_000
        )
        == "localization.unavailable"
    )
    assert (
        validate_spatial_transform(
            *common, 0.02, 0.03, 0.25, 0.35, 1_000_000_000, 500_000_000
        )
        == "localization.stale"
    )
