from pathlib import Path
import sys
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))


ROOT = Path(__file__).parents[1]


class ReleasePackagingTests(unittest.TestCase):
    def test_project_metadata_exposes_only_supported_commands(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        scripts = project["scripts"]
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertEqual(scripts["ump-inspector"], "ump.cli:inspector_main")
        self.assertEqual(scripts["ump-integration"], "ump.cli:integration_main")
        self.assertEqual(scripts["ump-node"], "ump.cli:node_main")
        self.assertEqual(scripts["ump-coordinator"], "ump.cli:coordinator_main")
        self.assertEqual(scripts["ump-lab"], "ump.cli:lab_main")
        self.assertEqual(scripts["ump-readiness"], "ump.cli:readiness_main")
        for removed in (
            "ump-pilot",
            "ump-public-readiness",
            "ump-release-evidence",
            "ump-review",
            "ump-ros2-evidence",
            "ump-simulate",
            "ump-visual-sim",
        ):
            self.assertNotIn(removed, scripts)

    def test_source_manifest_contains_core_assets_only(self):
        manifest = (ROOT / "MANIFEST.in").read_text()
        for required in (
            "recursive-include conformance *.json *.hex",
            "recursive-include docs *.md",
            "recursive-include examples *.py",
            "recursive-include schemas *.json",
        ):
            self.assertIn(required, manifest)
        self.assertNotIn("compliance", manifest)
        self.assertNotIn("ros2_ws", manifest)

    def test_reference_and_contribution_policies_are_packaged(self):
        self.assertTrue((ROOT / "REFERENCE.md").is_file())
        contributing = (ROOT / "CONTRIBUTING.md").read_text()
        self.assertIn("Conventional Commits", contributing)
        self.assertIn("Filename conventions", contributing)


if __name__ == "__main__":
    unittest.main()
