from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.runtime import CommunicationWatchdog, Registry
from ump.transport import InMemoryBus, make_envelope


class RecordingPolicy:
    def __init__(self, fail_once=False):
        self.events = []
        self.fail_once = fail_once

    def communication_lost(self, stale_peer_ids, observed_at_ms):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("manufacturer policy unavailable")
        self.events.append(("lost", stale_peer_ids, observed_at_ms))

    def communication_restored(self, restored_peer_ids, observed_at_ms):
        self.events.append(("restored", restored_peer_ids, observed_at_ms))


def publish_peer(bus, timestamp_ms=1_000, fresh_for_ms=100):
    bus.publish(
        make_envelope(
            "manifest",
            "peer-robot",
            "peer-session",
            1,
            timestamp_ms,
            {
                "robot_id": "peer-robot",
                "manufacturer": "UMP Test",
                "model": "P1",
                "robot_class": "other",
                "adapter_version": "0.1.0",
                "capabilities": [],
            },
        )
    )
    bus.publish(
        make_envelope(
            "state",
            "peer-robot",
            "peer-session",
            2,
            timestamp_ms,
            {
                "robot_id": "peer-robot",
                "mode": "idle",
                "safety": "normal",
                "activity": "Available",
                "intent": "Continue publishing state",
                "progress": 0.0,
                "summary": "Peer robot is available.",
                "fresh_for_ms": fresh_for_ms,
                "blockers": [],
                "resources": [],
                "assignment_id": None,
                "pose": None,
                "sensor_references": [],
            },
        )
    )


class CommunicationWatchdogTests(unittest.TestCase):
    def test_loss_and_restoration_are_edge_triggered_from_state_freshness(self):
        bus = InMemoryBus()
        registry = Registry(bus)
        policy = RecordingPolicy()
        watchdog = CommunicationWatchdog(registry, ("peer-robot",), policy)

        self.assertEqual(watchdog.evaluate(1_000), ("peer-robot",))
        self.assertEqual(watchdog.evaluate(1_001), ("peer-robot",))
        self.assertEqual(len(policy.events), 1)

        publish_peer(bus)
        self.assertEqual(watchdog.evaluate(1_050), ())
        self.assertEqual(policy.events[-1], ("restored", ("peer-robot",), 1_050))

        self.assertEqual(watchdog.evaluate(1_101), ("peer-robot",))
        self.assertEqual(policy.events[-1], ("lost", ("peer-robot",), 1_101))

    def test_failed_manufacturer_callback_is_observable_and_retried(self):
        registry = Registry(InMemoryBus())
        policy = RecordingPolicy(fail_once=True)
        watchdog = CommunicationWatchdog(registry, ("peer-robot",), policy)

        self.assertEqual(watchdog.evaluate(1_000), ())
        self.assertEqual(len(watchdog.errors), 1)
        self.assertEqual(watchdog.evaluate(1_001), ("peer-robot",))
        self.assertEqual(policy.events, [("lost", ("peer-robot",), 1_001)])

    def test_configuration_is_bounded_and_unambiguous(self):
        registry = Registry(InMemoryBus())
        policy = RecordingPolicy()
        with self.assertRaisesRegex(ValueError, "non-empty and unique"):
            CommunicationWatchdog(registry, (), policy)
        with self.assertRaisesRegex(ValueError, "non-empty and unique"):
            CommunicationWatchdog(registry, ("peer", "peer"), policy)
        with self.assertRaisesRegex(ValueError, "between 0.05 and 60"):
            CommunicationWatchdog(
                registry, ("peer",), policy, check_interval_s=0.01
            )


if __name__ == "__main__":
    unittest.main()
