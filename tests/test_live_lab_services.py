import unittest

from ump.lab_services import websocket_round_trip


class LiveLabServiceTests(unittest.TestCase):
    def test_massrobotics_websocket_round_trip_uses_a_real_socket(self):
        try:
            result = websocket_round_trip({"identity": "robot-1", "health": "healthy"})
        except Exception as error:
            if "websockets package" in str(error):
                self.skipTest(str(error))
            raise
        self.assertEqual(result["sent"], result["received"])
        self.assertTrue(result["response"]["accepted"])


if __name__ == "__main__":
    unittest.main()
