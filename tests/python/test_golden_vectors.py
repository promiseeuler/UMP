import pathlib
import unittest

from ump.v1 import core_pb2, machine_pb2, task_pb2


VECTORS = pathlib.Path(__file__).resolve().parents[1] / "vectors"


def decode(name: str) -> core_pb2.Envelope:
    envelope = core_pb2.Envelope()
    envelope.ParseFromString((VECTORS / name).read_bytes())
    return envelope


class GoldenVectorTests(unittest.TestCase):
    def test_hello(self) -> None:
        envelope = decode("hello.bin")
        self.assertEqual(envelope.protocol_major, 1)
        self.assertEqual(envelope.source_machine_id, "ump:machine:arm-vector")
        self.assertEqual(envelope.hello.supported_major_versions, [1])
        self.assertEqual(envelope.hello.presence_ttl_ms, 2_000)

    def test_advertisement(self) -> None:
        descriptor = decode("advertisement.bin").advertisement.descriptor
        self.assertEqual(descriptor.machine_class, "robot_arm")
        self.assertEqual(descriptor.endpoints[0].transport, machine_pb2.TRANSPORT_KIND_QUIC)
        self.assertEqual(descriptor.capabilities[0].type, "org.ump.material.pick")
        self.assertEqual(descriptor.capabilities[0].revision, 7)

    def test_gateway_proxy_descriptor_round_trip(self) -> None:
        descriptor = machine_pb2.MachineDescriptor(
            machine_class="robot_arm",
            revision=1,
            deployment_mode=machine_pb2.DEPLOYMENT_MODE_GATEWAY_PROXY,
            proxy=machine_pb2.ProxyAssociation(
                gateway_id="ump:gateway:test",
                represented_machine_id="ump:machine:arm-test",
                controller_interface="vendor_https_v1",
                read_only=True,
            ),
        )
        decoded = machine_pb2.MachineDescriptor.FromString(descriptor.SerializeToString())
        self.assertEqual(decoded.proxy.gateway_id, "ump:gateway:test")
        self.assertTrue(decoded.proxy.read_only)

    def test_state(self) -> None:
        state = decode("state.bin").state_update
        self.assertEqual(state.operational, machine_pb2.OPERATIONAL_STATE_IDLE)
        self.assertEqual(state.safety, machine_pb2.SAFETY_STATE_NORMAL)
        self.assertEqual(state.telemetry[0].metric, "controller.temperature")
        self.assertEqual(state.telemetry[0].value, 41.5)

    def test_task_request(self) -> None:
        envelope = decode("task-request.bin")
        self.assertEqual(envelope.correlation_id, "correlation-vector-task-request-1")
        self.assertEqual(envelope.task_request.task_id, "ump:task:delivery-vector-1")
        self.assertEqual(
            envelope.task_request.idempotency_policy,
            task_pb2.IDEMPOTENCY_POLICY_AT_MOST_ONCE,
        )
        self.assertEqual(envelope.task_request.maximum_attempts, 1)

    def test_handoff_proposal(self) -> None:
        handoff = decode("handoff-proposal.bin").handoff_proposal
        self.assertEqual(handoff.handoff_id, "ump:handoff:vector-1")
        self.assertEqual(
            handoff.transfer_context.reference_frame_id,
            "ump:frame:transfer-zone",
        )
        self.assertEqual(handoff.transfer_context.pose.position_m.x, 1.25)
        self.assertEqual(handoff.transfer_context.pose.orientation.w, 1.0)


if __name__ == "__main__":
    unittest.main()
