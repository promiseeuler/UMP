from pathlib import Path
import sys
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))


ROOT = Path(__file__).parents[1]


class ReleasePackagingTests(unittest.TestCase):
    def test_project_metadata_declares_runtime_and_license(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(metadata["name"], "universal-machine-protocol")
        self.assertEqual(metadata["license"], "Apache-2.0")
        self.assertEqual(metadata["requires-python"], ">=3.11")
        self.assertIn("cryptography>=43,<47", metadata["dependencies"])
        self.assertEqual(
            metadata["scripts"]["ump-adapter-conformance"],
            "ump.cli:adapter_conformance_main",
        )
        self.assertEqual(metadata["scripts"]["ump-reconcile"], "ump.cli:reconcile_main")
        self.assertEqual(
            metadata["scripts"]["ump-coordinator"], "ump.cli:coordinator_main"
        )
        self.assertEqual(metadata["scripts"]["ump-pilot"], "ump.cli:pilot_main")
        self.assertEqual(
            metadata["scripts"]["ump-ros2-evidence"],
            "ump.cli:ros2_evidence_main",
        )
        self.assertEqual(metadata["scripts"]["ump-review"], "ump.cli:review_main")
        self.assertEqual(
            metadata["scripts"]["ump-network-config"],
            "ump.cli:network_config_main",
        )
        self.assertEqual(metadata["scripts"]["ump-goal"], "ump.cli:goal_main")
        self.assertEqual(
            metadata["scripts"]["ump-lan-benchmark"],
            "ump.cli:lan_benchmark_main",
        )
        self.assertEqual(
            metadata["scripts"]["ump-lan-evidence"],
            "ump.cli:lan_evidence_main",
        )

    def test_source_manifest_contains_auditable_project_assets(self):
        manifest = (ROOT / "MANIFEST.in").read_text()
        for required in (
            "recursive-include compliance *.json",
            "recursive-include conformance *.json *.hex",
            "recursive-include docs *.md",
            "recursive-include examples *.py",
            "graft ros2_ws",
            "recursive-include schemas *.json",
        ):
            self.assertIn(required, manifest)
        for generated in ("build", "install", "log"):
            self.assertIn(f"prune ros2_ws/{generated}", manifest)

    def test_release_workflow_validates_installed_wheel_and_source_archive(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        self.assertIn("python -m twine check dist/*", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-demo", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-adapter-conformance --help", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-adapter-conformance schema", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-lan-benchmark --help", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-lan-evidence schema", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-node --help", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-ros2-evidence --help", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-review schema", workflow)
        self.assertIn("/tmp/ump-release/bin/ump-goal schema", workflow)
        self.assertIn("schemas/ump-v0.schema.json", workflow)
        self.assertIn("schemas/ump-lan-evidence-v1.schema.json", workflow)
        self.assertIn("schemas/ump-independent-review-v1.schema.json", workflow)
        self.assertIn("schemas/ump-adapter-conformance-v1.schema.json", workflow)
        self.assertIn("schemas/ump-network-config-v1.schema.json", workflow)
        self.assertIn("schemas/ump-shared-goal-v1.schema.json", workflow)
        self.assertIn("schemas/ump-shared-goal-batch-v1.schema.json", workflow)
        self.assertIn("vocabulary_data/v1/catalog.json", workflow)
        self.assertIn("examples/read_only_adapter.py", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("actions/attest@v4", workflow)
        self.assertIn("github.event.repository.visibility == 'public'", workflow)
        self.assertNotIn("pypi", workflow.lower())


if __name__ == "__main__":
    unittest.main()
