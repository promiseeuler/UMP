"""Optional live transport probes for the UMP standards laboratory."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import threading
import time
from typing import Any

from .integrations.base import IntegrationUnavailableError


def websocket_round_trip(document: dict[str, Any]) -> dict[str, Any]:
    try:
        from websockets.sync.client import connect
        from websockets.sync.server import serve
    except ImportError as error:
        raise IntegrationUnavailableError(
            "MassRobotics live probe requires the optional websockets package"
        ) from error
    received: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def handler(websocket) -> None:
        message = json.loads(websocket.recv())
        received.put(message)
        websocket.send(json.dumps({"accepted": True, "identity": message.get("identity")}))

    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with connect(f"ws://127.0.0.1:{server.socket.getsockname()[1]}") as websocket:
            websocket.send(json.dumps(document))
            response = json.loads(websocket.recv())
        server.shutdown()
        thread.join(timeout=2)
    return {"sent": document, "received": received.get_nowait(), "response": response}


def mqtt_round_trip(document: dict[str, Any], endpoint: str, topic: str) -> dict[str, Any]:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as error:
        raise IntegrationUnavailableError(
            "VDA 5050 live probe requires the optional paho-mqtt package"
        ) from error
    host, _, port_text = endpoint.partition(":")
    port = int(port_text or "1883")
    received: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        del userdata, flags, properties
        if reason_code != 0:
            received.put({"error": f"connect failed: {reason_code}"})
        client.subscribe(topic, qos=1)

    def on_message(client, userdata, message) -> None:
        del client, userdata
        received.put(json.loads(message.payload.decode("utf-8")))

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, keepalive=10)
    client.loop_start()
    try:
        deadline = time.monotonic() + 5
        while client.is_connected() is False and time.monotonic() < deadline:
            time.sleep(0.01)
        publication = client.publish(topic, json.dumps(document), qos=1)
        publication.wait_for_publish(timeout=5)
        observed = received.get(timeout=5)
        if "error" in observed:
            raise ConnectionError(observed["error"])
        return {"sent": document, "received": observed, "topic": topic}
    finally:
        client.disconnect()
        client.loop_stop()


async def _opcua_round_trip(document: dict[str, Any]) -> dict[str, Any]:
    try:
        from asyncua import Client, Server
    except ImportError as error:
        raise IntegrationUnavailableError(
            "OPC UA live probe requires the optional asyncua package"
        ) from error
    server = Server()
    await server.init()
    endpoint = "opc.tcp://127.0.0.1:0/ump/lab/"
    server.set_endpoint(endpoint)
    namespace = await server.register_namespace("urn:ump:lab")
    robot = await server.nodes.objects.add_object(namespace, "Robot")
    state = await robot.add_variable(namespace, "SemanticState", json.dumps(document))
    await server.start()
    try:
        sockets = getattr(server.bserver, "_server", None).sockets
        port = sockets[0].getsockname()[1]
        async with Client(f"opc.tcp://127.0.0.1:{port}/ump/lab/") as client:
            node = client.get_node(state.nodeid)
            observed = json.loads(await node.read_value())
        return {"sent": document, "received": observed, "node_id": state.nodeid.to_string()}
    finally:
        await server.stop()


def opcua_round_trip(document: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_opcua_round_trip(document))


def ros2_round_trip(document: dict[str, Any]) -> dict[str, Any]:
    try:
        import rclpy
        from std_msgs.msg import String
    except ImportError as error:
        raise IntegrationUnavailableError(
            "ROS 2 live probe requires rclpy and std_msgs"
        ) from error
    rclpy.init(args=None)
    node = rclpy.create_node("ump_lab_round_trip")
    received: list[str] = []
    subscription = node.create_subscription(String, "/ump/lab/semantic_state", lambda message: received.append(message.data), 10)
    publisher = node.create_publisher(String, "/ump/lab/semantic_state", 10)
    try:
        deadline = time.monotonic() + 5
        message = String(data=json.dumps(document, sort_keys=True))
        while not received and time.monotonic() < deadline:
            publisher.publish(message)
            rclpy.spin_once(node, timeout_sec=0.1)
        if not received:
            raise TimeoutError("ROS 2 semantic state did not round trip")
        return {"sent": document, "received": json.loads(received[0]), "topic": "/ump/lab/semantic_state"}
    finally:
        node.destroy_subscription(subscription)
        node.destroy_node()
        rclpy.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ump.lab_services")
    parser.add_argument("standard", choices=("massrobotics", "vda5050", "opc_ua", "ros2"))
    parser.add_argument("--mqtt-endpoint", default=os.environ.get("UMP_MQTT_ENDPOINT", "127.0.0.1:1883"))
    arguments = parser.parse_args(argv)
    document = {"identity": "ump-lab-robot", "sequence": 1, "health": "healthy", "battery": 0.8}
    try:
        if arguments.standard == "massrobotics":
            result = websocket_round_trip(document)
        elif arguments.standard == "vda5050":
            result = mqtt_round_trip(document, arguments.mqtt_endpoint, "uagv/v3/UMP/lab/state")
        elif arguments.standard == "opc_ua":
            result = opcua_round_trip(document)
        else:
            result = ros2_round_trip(document)
        print(json.dumps({"passed": result["sent"] == result["received"], **result}, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"passed": False, "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
