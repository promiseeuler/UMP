# Planner Integration

UMP planners convert a shared goal and authorized robot descriptions into a
proposed high-level plan. They do not invoke robot APIs, control actuators, or
replace robot-local autonomy and safety. A planner may be deterministic,
human-backed, or use a reasoning model.

## Contract

Implement `ump.Planner`:

```python
from pathlib import Path

from ump import Plan, Planner, RobotManifest, RobotState, SharedGoal


class SitePlanner:
    planner_id = "example.site/planner/v1"

    def propose(
        self,
        goal: SharedGoal,
        manifests: dict[str, RobotManifest],
        states: dict[str, RobotState],
    ) -> Plan:
        ...


def create_planner(config_path: Path | None) -> Planner:
    return SitePlanner()
```

The factory is loaded from an explicitly configured trusted Python import:

```python
from ump import load_planner

planner = load_planner("site_planner:create_planner", "planner.json")
```

Factories receive a `Path` when a configuration path is supplied and `None`
otherwise. `planner_id` must be a stable, non-empty, namespaced identifier. A
planner should generate a new `plan_id` for each proposal and preserve the
required revision lineage when replanning.

## Trust Boundary

The coordinator supplies only the goal's declared participants and only the
manifest/state fields already authorized by network disclosure policy. Planner
implementations must treat this snapshot as potentially incomplete and must not
infer that absent capabilities or state are available.

Every returned plan is untrusted. Before publishing assignments, the coordinator
validates message size, goal identity, participants, fresh state, capability
availability, deadlines, dependencies, cycles, and authority leases. Each robot
then independently validates its local lease, capability input schema, resource
availability, and safety policy. A planner cannot bypass those checks.

Reasoning providers should return concise semantic task descriptions and
structured capability inputs. Raw sensor streams, native controller handles,
credentials, and actuator commands are outside this interface.

## Model-Backed Planners

A model-backed implementation owns model selection, prompting, authentication,
timeouts, and response parsing. It must convert the response into `ump.Plan` and
surface provider failures without inventing assignments. UMP does not require a
specific model provider and does not consider model output trusted or safe.

The deterministic reference factory is available as
`ump.demo:create_warehouse_planner` for integration tests and examples.
