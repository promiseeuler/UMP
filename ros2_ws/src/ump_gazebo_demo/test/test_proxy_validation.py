import pytest

from ump_gazebo_demo.proxy_action_server import successful_outputs, valid_goal_request


EXPECTED = "ump.navigation.inspect-route/v1"


@pytest.mark.parametrize(
    ("assignment_id", "capability", "inputs_json"),
    (
        ("", EXPECTED, "{}"),
        ("assignment-1", "ump.material.carry/v1", "{}"),
        ("assignment-1", EXPECTED, "not-json"),
        ("assignment-1", EXPECTED, "[]"),
        ("assignment-1", EXPECTED, "null"),
    ),
)
def test_invalid_goal_requests_fail_closed(assignment_id, capability, inputs_json):
    assert not valid_goal_request(EXPECTED, assignment_id, capability, inputs_json)


def test_matching_capability_and_object_inputs_are_valid():
    assert valid_goal_request(
        EXPECTED,
        "assignment-1",
        EXPECTED,
        '{"zone":"aisle-a"}',
    )


def test_inspect_route_success_has_machine_readable_outputs():
    assert successful_outputs(EXPECTED, {"route": "aisle-a"}) == {
        "completed": True,
        "traversable": True,
        "summary": "The requested route is traversable",
    }


@pytest.mark.parametrize(
    ("capability", "inputs", "field", "value"),
    (
        ("ump.material.carry/v1", {"destination": "dock"}, "final_location", "dock"),
        ("ump.manipulation.place/v1", {"target": "bench"}, "target", "bench"),
    ),
)
def test_material_and_manipulation_success_preserve_target(
    capability, inputs, field, value
):
    outputs = successful_outputs(capability, inputs)
    assert outputs["completed"] is True
    assert outputs[field] == value
