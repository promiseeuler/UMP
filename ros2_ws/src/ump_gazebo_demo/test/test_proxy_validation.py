import pytest

from ump_gazebo_demo.proxy_action_server import valid_goal_request


EXPECTED = "ump.navigation.inspect/v1"


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
