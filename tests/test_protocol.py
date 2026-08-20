import sys
import json
import unittest
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.collaboration import Coordinator, PlanValidationError, validate_plan
from ump.demo import WarehousePlanner, build_demo
from ump.models import Assignment, PlanStep, RobotState, Safety, SharedGoal
from ump.transport import (
    ProtocolDecodeError,
    SAFETY_STREAM,
    decode_envelope,
    encode_envelope,
    make_envelope,
)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.bus, self.registry, self.participants = build_demo()
        self.goal = SharedGoal(
            "goal-1",
            "Move the package",
            tuple(self.registry.peers),
            deadline_ms=60_000,
        )

    def test_three_robots_share_manifests_and_state(self):
        self.assertEqual(len(self.registry.peers), 3)
        for peer in self.registry.peers.values():
            self.assertIsNotNone(peer.manifest)
            self.assertIsNotNone(peer.state)
            self.assertTrue(peer.state.summary)

    def test_stale_or_replayed_state_is_ignored(self):
        peer = self.registry.peers["robot-humanoid-1"]
        original = peer.state
        replay = make_envelope(
            "state",
            "robot-humanoid-1",
            peer.session_id,
            1,
            1_500,
            {"summary": "invalid replay"},
        )
        self.bus.publish(replay)
        self.assertIs(peer.state, original)

    def test_valid_plan_executes_in_dependency_order(self):
        coordinator = Coordinator(
            "coordinator", self.bus, self.registry, require_authority=False
        )
        plan = coordinator.execute(self.goal, WarehousePlanner(), 1_100)
        assignments = [item for item in self.bus.trace if item.message_type == "assignment"]
        self.assertEqual(
            [item.payload["step"]["step_id"] for item in assignments],
            [step.step_id for step in plan.steps],
        )
        self.assertEqual(len(coordinator.outcomes), 3)
        self.assertTrue(all(coordinator.outcomes.values()))
        coordinator.close()

    def test_unadvertised_capability_is_rejected(self):
        plan = WarehousePlanner().propose(self.goal, {}, {})
        bad_step = replace(plan.steps[0], capability="vendor.private.fly/v1")
        invalid = replace(plan, steps=(bad_step,) + plan.steps[1:])
        with self.assertRaisesRegex(PlanValidationError, "does not have available"):
            validate_plan(self.goal, invalid, self.registry, 1_100)

    def test_dependency_cycle_is_rejected(self):
        plan = WarehousePlanner().propose(self.goal, {}, {})
        first = replace(plan.steps[0], depends_on=("place-package",))
        invalid = replace(plan, steps=(first,) + plan.steps[1:])
        with self.assertRaisesRegex(PlanValidationError, "dependency cycle"):
            validate_plan(self.goal, invalid, self.registry, 1_100)

    def test_state_enforces_semantic_bounds(self):
        state = self.registry.peers["robot-humanoid-1"].state
        with self.assertRaisesRegex(ValueError, "progress"):
            RobotState(**{**state.__dict__, "progress": 1.1})

    def test_envelope_has_canonical_wire_round_trip(self):
        envelope = self.bus.trace[0]
        self.assertEqual(decode_envelope(encode_envelope(envelope)), envelope)
        self.assertEqual(encode_envelope(envelope), encode_envelope(envelope))

    def test_safety_state_uses_independent_sequence_and_newer_timestamp_wins(self):
        operational = next(
            item
            for item in self.bus.trace
            if item.message_type == "state" and item.source_id == "robot-humanoid-1"
        )
        safety = replace(
            operational,
            message_id="safety-state",
            stream=SAFETY_STREAM,
            sequence=1,
            timestamp_ms=2_000,
            payload={**operational.payload, "safety": "protective_stop"},
        )
        self.bus.publish(safety)
        peer = self.registry.peers[operational.source_id]
        self.assertEqual(peer.state.safety.value, "protective_stop")
        delayed = replace(
            operational,
            message_id="delayed-operational-state",
            sequence=3,
            timestamp_ms=1_500,
        )
        self.bus.publish(delayed)
        self.assertEqual(peer.state.safety.value, "protective_stop")
        self.assertEqual(peer.last_sequences[SAFETY_STREAM], 1)

    def test_participant_safety_publication_has_independent_counter(self):
        participant = self.participants[0]
        participant.publish_safety_state(2_000)
        safety = self.bus.trace[-1]
        self.assertEqual(safety.stream, SAFETY_STREAM)
        self.assertEqual(safety.message_type, "state")
        self.assertEqual(safety.sequence, 1)
        participant.publish_state(2_001)
        self.assertEqual(self.bus.trace[-1].stream, "operational")
        self.assertEqual(self.bus.trace[-1].sequence, 3)

    def test_participant_rejects_supplied_state_for_another_robot(self):
        participant = self.participants[0]
        foreign_state = replace(participant.adapter.state(), robot_id="robot-other")
        with self.assertRaisesRegex(ValueError, "identity differs"):
            participant.publish_state(2_000, state=foreign_state)

    def test_participant_rejects_supplied_manifest_for_another_robot(self):
        participant = self.participants[0]
        foreign_manifest = replace(
            participant.adapter.manifest(), robot_id="robot-other"
        )
        with self.assertRaisesRegex(ValueError, "identity differs"):
            participant.publish_manifest(2_000, manifest=foreign_manifest)

    def test_participant_rejects_invalid_dynamic_capability_schema(self):
        participant = self.participants[0]
        manifest = participant.adapter.manifest()
        invalid_capability = replace(
            manifest.capabilities[0], input_schema={"type": "not-a-json-type"}
        )
        invalid_manifest = replace(
            manifest,
            capabilities=(invalid_capability,) + manifest.capabilities[1:],
        )
        trace_length = len(self.bus.trace)
        with self.assertRaises(SchemaError):
            participant.publish_manifest(2_000, manifest=invalid_manifest)
        self.assertEqual(len(self.bus.trace), trace_length)

    def test_operational_publication_emits_changed_safety_on_priority_stream_first(self):
        participant = self.participants[0]
        changed = replace(
            participant.adapter.state(), safety=Safety.PROTECTIVE_STOP
        )
        participant.publish_state(2_000, state=changed)
        safety, operational = self.bus.trace[-2:]
        self.assertEqual(safety.stream, SAFETY_STREAM)
        self.assertEqual(operational.stream, "operational")
        self.assertEqual(safety.payload, operational.payload)
        self.assertEqual(safety.sequence, 1)
        self.assertEqual(operational.sequence, 3)

    def test_safety_stream_rejects_non_state_messages(self):
        envelope = make_envelope(
            "assignment",
            "coordinator",
            "session-1",
            1,
            1_000,
            {},
            stream=SAFETY_STREAM,
        )
        with self.assertRaisesRegex(ProtocolDecodeError, "state messages only"):
            encode_envelope(envelope)

    def test_all_demo_messages_conform_to_published_schema(self):
        coordinator = Coordinator(
            "coordinator", self.bus, self.registry, require_authority=False
        )
        coordinator.execute(self.goal, WarehousePlanner(), 1_100)
        schema_path = Path(__file__).parents[1] / "schemas" / "ump-v0.schema.json"
        validator = Draft202012Validator(json.loads(schema_path.read_text()))
        for envelope in self.bus.trace:
            with self.subTest(message_type=envelope.message_type):
                validator.validate(json.loads(encode_envelope(envelope)))
        coordinator.close()

    def test_wire_codec_rejects_oversized_message(self):
        envelope = make_envelope(
            "goal",
            "coordinator",
            "session-1",
            1,
            1_000,
            {"value": "x" * 70_000},
        )
        with self.assertRaisesRegex(ProtocolDecodeError, "64 KiB"):
            encode_envelope(envelope)

    def test_oversized_goal_is_rejected_before_planner_or_publication(self):
        class PlannerThatMustNotRun:
            planner_id = "ump.test.never/v1"

            def propose(self, goal, manifests, states):
                raise AssertionError("oversized goal reached planner")

        goal = SharedGoal(
            "oversized-goal",
            "Move the package",
            tuple(self.registry.peers),
            constraints={"data": "x" * 70_000},
        )
        coordinator = Coordinator(
            "coordinator", self.bus, self.registry, require_authority=False
        )
        before = len(self.bus.trace)
        with self.assertRaisesRegex(PlanValidationError, "64 KiB"):
            coordinator.submit(goal, PlannerThatMustNotRun(), 1_100)
        self.assertEqual(len(self.bus.trace), before)
        coordinator.close()

    def test_oversized_plan_is_rejected_before_journal_or_publication(self):
        class OversizedPlanner:
            planner_id = "ump.test.oversized/v1"

            def propose(self, goal, manifests, states):
                del manifests, states
                plan = WarehousePlanner().propose(goal, {}, {})
                step = replace(plan.steps[0], inputs={"data": "x" * 70_000})
                return replace(plan, steps=(step,) + plan.steps[1:])

        coordinator = Coordinator(
            "coordinator", self.bus, self.registry, require_authority=False
        )
        before = len(self.bus.trace)
        with self.assertRaisesRegex(PlanValidationError, "64 KiB"):
            coordinator.submit(self.goal, OversizedPlanner(), 1_100)
        self.assertEqual(len(self.bus.trace), before)
        coordinator.close()

    def test_goal_batch_is_fully_validated_before_publication(self):
        class BatchPlanner:
            planner_id = "ump.test.batch/v1"

            def __init__(self):
                self.plan_ids = []

            def propose(self, goal, manifests, states):
                del manifests, states
                plan = WarehousePlanner().propose(goal, {}, {})
                plan = replace(plan, plan_id=f"plan-{goal.goal_id}")
                self.plan_ids.append(plan.plan_id)
                if goal.goal_id == "goal-invalid":
                    bad = replace(plan.steps[0], capability="ump.unknown/v1")
                    plan = replace(plan, steps=(bad,) + plan.steps[1:])
                return plan

        goals = (
            replace(self.goal, goal_id="goal-valid"),
            replace(self.goal, goal_id="goal-invalid"),
        )
        planner = BatchPlanner()
        coordinator = Coordinator(
            "coordinator", self.bus, self.registry, require_authority=False
        )
        before = len(self.bus.trace)
        with self.assertRaisesRegex(PlanValidationError, "does not have available"):
            coordinator.submit_many(goals, planner, 1_100)
        self.assertEqual(len(self.bus.trace), before)
        with self.assertRaises(KeyError):
            coordinator.snapshot(planner.plan_ids[0])
        coordinator.close()

    def test_goal_batch_executes_each_goal_through_public_api(self):
        goals = (
            replace(self.goal, goal_id="goal-batch-1"),
            replace(self.goal, goal_id="goal-batch-2"),
        )
        coordinator = Coordinator(
            "coordinator", self.bus, self.registry, require_authority=False
        )
        plans = coordinator.execute_many(goals, WarehousePlanner(), 1_100)
        self.assertEqual([plan.goal_id for plan in plans], [goal.goal_id for goal in goals])
        self.assertTrue(
            all(coordinator.snapshot(plan.plan_id).status.value == "succeeded" for plan in plans)
        )
        coordinator.close()

    def test_registry_rejects_spoofed_manifest_identity(self):
        manifest = self.bus.trace[0]
        spoofed = replace(manifest, source_id="different-robot", sequence=100)
        with self.assertRaisesRegex(ValueError, "authenticated source"):
            self.bus.publish(spoofed)

    def test_new_session_requires_manifest_before_state(self):
        state = next(item for item in self.bus.trace if item.message_type == "state")
        restarted = replace(state, session_id="new-session", sequence=1)
        with self.assertRaisesRegex(ValueError, "before manifest"):
            self.bus.publish(restarted)

    def test_assignment_input_is_validated_and_duplicate_is_not_reexecuted(self):
        participant = self.participants[0]
        step = PlanStep(
            "bad-input",
            "Carry an object",
            participant.robot_id,
            "ump.material.carry/v1",
            "not-an-object",
            "Object reaches destination",
        )
        assignment = Assignment("assignment-1", "goal-1", "plan-1", step)
        coordinator = Coordinator(
            "coordinator", self.bus, self.registry, require_authority=False
        )
        coordinator._publish("assignment", assignment, 1_100, "goal-1")
        coordinator._publish("assignment", assignment, 1_101, "goal-1")
        outcomes = [
            item
            for item in self.bus.trace
            if item.message_type == "outcome"
            and item.payload["assignment_id"] == "assignment-1"
        ]
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(not item.payload["succeeded"] for item in outcomes))
        coordinator.close()


if __name__ == "__main__":
    unittest.main()
