import asyncio
import unittest

from ump.task import HandlerRegistry, TaskContext


class TaskHandlerTests(unittest.TestCase):
    def context(self) -> TaskContext:
        return TaskContext(
            task_id="ump:task:test-1",
            issuer_machine_id="ump:machine:coordinator",
            capability="org.ump.logistics.deliver",
            input=b"package-1",
            input_content_type="application/octet-stream",
            attempt=1,
            deadline_ms=10_000,
            correlation_id="correlation-test-1",
        )

    def test_sync_handler(self) -> None:
        registry = HandlerRegistry()
        calls = []

        def handler(context: TaskContext) -> bytes:
            calls.append(context.task_id)
            return b"delivered"

        registry.register(
            "org.ump.logistics.deliver", handler, interruptible=True
        )
        self.assertEqual(asyncio.run(registry.execute(self.context())), b"delivered")
        self.assertEqual(calls, ["ump:task:test-1"])

    def test_async_handler_and_duplicate_registration(self) -> None:
        registry = HandlerRegistry()

        async def handler(context: TaskContext) -> bytes:
            return context.input

        registry.register(
            "org.ump.logistics.deliver", handler, interruptible=False
        )
        with self.assertRaises(ValueError):
            registry.register(
                "org.ump.logistics.deliver", handler, interruptible=False
            )
        self.assertEqual(asyncio.run(registry.execute(self.context())), b"package-1")

    def test_missing_handler_and_invalid_result(self) -> None:
        registry = HandlerRegistry()
        with self.assertRaises(KeyError):
            asyncio.run(registry.execute(self.context()))

        registry.register(
            "org.ump.logistics.deliver",
            lambda _context: "not bytes",  # type: ignore[return-value]
            interruptible=False,
        )
        with self.assertRaises(TypeError):
            asyncio.run(registry.execute(self.context()))


if __name__ == "__main__":
    unittest.main()
