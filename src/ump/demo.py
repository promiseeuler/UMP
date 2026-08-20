from __future__ import annotations

from uuid import uuid4

from .authority import AllowAllAuthorizer
from .collaboration import Coordinator
from .models import Plan, PlanStep, RobotManifest, SharedGoal
from .runtime import Participant, Registry
from .simulation import SimulatedRobot, capability
from .transport import InMemoryBus


class WarehousePlanner:
    planner_id = "ump.reference.warehouse-planner/v1"

    def propose(self, goal, manifests, states) -> Plan:
        del manifests, states
        return Plan(
            plan_id=str(uuid4()),
            goal_id=goal.goal_id,
            planner_id=self.planner_id,
            summary="Inspect the route, transport the package, then place it in storage.",
            steps=(
                PlanStep(
                    "inspect-route",
                    "Inspect the route from intake to storage",
                    "robot-quadruped-1",
                    "ump.navigation.inspect-route/v1",
                    {"from": "intake", "to": "storage"},
                    "A traversable route is published",
                    deadline_ms=50_000,
                ),
                PlanStep(
                    "carry-package",
                    "Carry the sealed package along the approved route",
                    "robot-humanoid-1",
                    "ump.material.carry/v1",
                    {"object": "package-1", "destination": "storage"},
                    "The package arrives at the storage handoff point",
                    ("inspect-route",),
                    deadline_ms=55_000,
                ),
                PlanStep(
                    "place-package",
                    "Place the sealed package on the storage shelf",
                    "robot-mobile-arm-1",
                    "ump.manipulation.place/v1",
                    {"object": "package-1", "target": "shelf-a"},
                    "Placement is confirmed at shelf A",
                    ("carry-package",),
                    deadline_ms=60_000,
                ),
            ),
        )


def build_demo():
    bus = InMemoryBus()
    registry = Registry(bus)
    manifests = (
        RobotManifest(
            "robot-humanoid-1",
            "Example Humanoid Co",
            "H1",
            "humanoid",
            (capability("ump.material.carry/v1", "Carry a bounded payload"),),
        ),
        RobotManifest(
            "robot-quadruped-1",
            "Example Quadruped Co",
            "Q1",
            "quadruped",
            (capability("ump.navigation.inspect-route/v1", "Inspect route traversability"),),
        ),
        RobotManifest(
            "robot-mobile-arm-1",
            "Example Manipulation Co",
            "A1",
            "mobile_arm",
            (capability("ump.manipulation.place/v1", "Place an object at a named target"),),
        ),
    )
    participants = [
        Participant(
            SimulatedRobot(item),
            bus,
            authorizer=AllowAllAuthorizer(),
            clock_ms=lambda: 1_100,
        )
        for item in manifests
    ]
    for participant in participants:
        participant.announce(now_ms=1_000)
    return bus, registry, participants


def main() -> None:
    bus, registry, _participants = build_demo()
    goal = SharedGoal(
        goal_id="goal-move-package-1",
        description="Move the sealed package from intake to storage shelf A",
        participant_ids=tuple(registry.peers),
        constraints={"keep_upright": True},
        deadline_ms=60_000,
    )
    coordinator = Coordinator(
        "ump-coordinator-1",
        bus,
        registry,
        authority_lease_ids={
            robot_id: "simulation-authority"
            for robot_id in goal.participant_ids
        },
    )
    plan = coordinator.execute(goal, WarehousePlanner(), now_ms=1_100)

    print(f"Goal: {goal.description}")
    print(f"Planner: {plan.planner_id}")
    for step in plan.steps:
        state = registry.peers[step.assigned_robot_id].state
        print(f"- {step.assigned_robot_id}: {state.summary}")
    print(f"Trace: {len(bus.trace)} UMP messages")
    coordinator.close()


if __name__ == "__main__":
    main()
