from pathlib import Path
import json
import tempfile
import unittest

from cryptography.exceptions import InvalidSignature

from ump.lab import (
    ControllerState,
    FaultInjector,
    FaultProfile,
    create_readiness_report,
    default_scenario,
    generate_signing_key,
    mixed_fleet_controllers,
    run_fault_probe,
    run_load_probe,
    run_scenario,
    sign_readiness_report,
    validate_fault_profile,
    validate_readiness_report,
    validate_scenario,
    verify_readiness_report,
)
from ump.cli import lab_main, readiness_main
from ump.models import Health, Safety


class HardwareLabTests(unittest.TestCase):
    def test_default_mixed_fleet_scenario_is_authorized_and_dependency_ordered(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_scenario(workspace=directory)
        self.assertTrue(result.passed, result.failure)
        self.assertEqual(
            result.step_statuses,
            {"transport": "succeeded", "inspect": "succeeded", "place": "succeeded"},
        )
        self.assertEqual(result.traces["mobile-1"][0]["event"], "accepted")
        self.assertEqual(result.traces["manipulator-1"][-1]["event"], "succeeded")

    def test_native_controller_rejects_depleted_faulted_and_emergency_states(self):
        setups = (
            lambda controllers: controllers["mobile-1"].set_battery_level(0.01),
            lambda controllers: controllers["mobile-1"].set_health(Health.FAULTED),
            lambda controllers: controllers["mobile-1"].set_safety(Safety.EMERGENCY_STOP),
            lambda controllers: controllers["mobile-1"].set_connected(False),
        )
        for setup in setups:
            with self.subTest(setup=setup), tempfile.TemporaryDirectory() as directory:
                result = run_scenario(workspace=directory, controller_setup=setup)
                self.assertFalse(result.passed)

    def test_unknown_battery_is_not_invented(self):
        controllers = mixed_fleet_controllers(lambda: 1000)
        state = controllers["manipulator-1"].state()
        self.assertIsNone(state.battery)
        self.assertEqual(controllers["manipulator-1"].controller_state, ControllerState.IDLE)

    def test_native_executor_is_used_through_the_robot_adapter_boundary(self):
        calls = []

        def setup(controllers):
            controllers["mobile-1"].set_native_executor(
                lambda assignment: calls.append(assignment.step.capability) or {"delivered": True}
            )

        with tempfile.TemporaryDirectory() as directory:
            result = run_scenario(workspace=directory, controller_setup=setup)
        self.assertTrue(result.passed, result.failure)
        self.assertEqual(calls, ["ump.material.carry/v1"])

    def test_fault_injector_is_deterministic_and_models_partition(self):
        profile = FaultProfile("chaos", seed=42, latency_ms=20, jitter_ms=5, duplicate_rate=0.5)
        first = [FaultInjector(profile).decide("mobile-1") for _ in range(2)]
        self.assertEqual(first[0], first[1])
        partition = FaultInjector(FaultProfile("partition", partitioned_sources=("mobile-1",)))
        self.assertEqual(partition.decide("mobile-1").reason, "partition")

    def test_reference_load_profiles_keep_every_participant_fresh(self):
        for participants in (3, 25, 100, 250):
            with self.subTest(participants=participants):
                result = run_load_probe(participants, cycles=2)
                self.assertEqual(result.stale_participants, 0)
                self.assertGreater(result.messages_per_second, 0)

    def test_scenario_fault_and_report_schemas_validate(self):
        scenario_path = Path("src/ump/lab_data/v1/warehouse-inspection-transfer.json")
        validate_scenario(json.loads(scenario_path.read_text()))
        validate_fault_profile({"profile": "ump-fault-profile/v1", "profile_id": "none"})
        with tempfile.TemporaryDirectory() as directory:
            result = run_scenario(workspace=directory)
            outcomes = run_fault_probe(FaultProfile("small", seed=7, loss_rate=0.1), 10)
            report = create_readiness_report(result, configuration={"scenario": result.scenario_id}, fault_outcomes=outcomes, generated_at_ms=2_000)
            signed = sign_readiness_report(report, generate_signing_key()).as_dict()
        validate_readiness_report(signed)
        verify_readiness_report(signed)
        signed["passed"] = not signed["passed"]
        with self.assertRaises(InvalidSignature):
            verify_readiness_report(signed)

    def test_lab_and_readiness_cli_create_verifiable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "scenario.json"
            report_path = Path(directory) / "readiness.json"
            self.assertEqual(
                lab_main(["run", "--workspace", directory, "--output", str(result_path)]),
                0,
            )
            self.assertTrue(json.loads(result_path.read_text())["passed"])
            self.assertEqual(
                readiness_main(["report", "--workspace", directory, "--output", str(report_path)]),
                0,
            )
            self.assertEqual(readiness_main(["verify", str(report_path)]), 0)
            self.assertEqual(readiness_main(["--validate-only"]), 0)


if __name__ == "__main__":
    unittest.main()
