from __future__ import annotations

from pathlib import Path
import stat
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativePackageTest(unittest.TestCase):
    def test_service_uses_persistent_least_privilege_runtime(self) -> None:
        service = (ROOT / "packaging/systemd/umpd.service").read_text()
        for directive in (
            "User=ump",
            "Group=ump",
            "Environment=UMP_DATA_DIR=/var/lib/ump",
            "StateDirectory=ump",
            "StateDirectoryMode=0700",
            "UMask=0077",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
        ):
            self.assertIn(directive, service)

    def test_maintainer_scripts_are_executable_and_preserve_state(self) -> None:
        for name in ("postinst", "prerm", "postrm"):
            path = ROOT / "packaging/debian" / name
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, name)
        postrm = (ROOT / "packaging/debian/postrm").read_text()
        self.assertNotIn("rm -rf /var/lib/ump", postrm)

    def test_container_is_rootless_and_persistent(self) -> None:
        dockerfile = (ROOT / "packaging/container/Dockerfile").read_text()
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn('ENV UMP_DATA_DIR=/var/lib/ump', dockerfile)
        self.assertIn('VOLUME ["/var/lib/ump"]', dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/local/bin/umpd"]', dockerfile)

    def test_release_workflow_signs_all_supported_artifacts(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        for requirement in (
            "ubuntu-24.04-arm",
            "architecture: amd64",
            "architecture: arm64",
            "cosign sign-blob --yes --bundle",
            "actions/attest-build-provenance@v2",
            "platforms: linux/amd64,linux/arm64",
            "provenance: mode=max",
            "sbom: true",
            "cosign sign --yes",
            'gh release create "$GITHUB_REF_NAME" --verify-tag',
        ):
            self.assertIn(requirement, workflow)

    def test_ros_package_uses_relocatable_final_prefix(self) -> None:
        dockerfile = (ROOT / "packaging/ros2/Dockerfile").read_text()
        builder = (ROOT / "packaging/ros2/build-deb.sh").read_text()
        verifier = (ROOT / "packaging/ros2/test-deb.sh").read_text()
        self.assertIn("--install-base /opt/ump/ros/jazzy", dockerfile)
        self.assertIn("Package: ump-ros2-jazzy", builder)
        self.assertIn("Depends: ump (>= $version)", builder)
        self.assertIn("development prefix leaked", verifier)
        self.assertIn("libump_payload_handoff.so", verifier)


if __name__ == "__main__":
    unittest.main()
