from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tambox_gateway.software_update import (
    SoftwareUpdateError,
    installed_version,
    latest_version,
    start_update,
    supports_updates,
    update_backend,
)


class _Response:
    def __init__(self, payload: bytes, final_url: str) -> None:
        self.payload = payload
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self.payload

    def geturl(self) -> str:
        return self.final_url


class SoftwareUpdateTests(unittest.TestCase):
    @patch("tambox_gateway.software_update.urlopen")
    def test_latest_version_reads_commit_from_public_patch(self, open_url) -> None:
        open_url.return_value = _Response(
            b"From 3aec36552bfb15883cb30b70db19f3152466fc3f Mon Sep 17 00:00:00 2001\n",
            "https://github.com/beahead-ab/trainmeet-server/commit/main.patch",
        )

        result = latest_version()

        self.assertEqual(result["version"], "3aec3655")
        self.assertIn("/commit/main.patch", open_url.call_args.args[0].full_url)

    @patch("tambox_gateway.software_update.urlopen")
    def test_invalid_patch_is_reported_clearly(self, open_url) -> None:
        open_url.return_value = _Response(
            b"not a commit patch",
            "https://github.com/beahead-ab/trainmeet-server/commit/main.patch",
        )

        with self.assertRaisesRegex(SoftwareUpdateError, "giltig versionsinformation"):
            latest_version()


class UpdateBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _mac_installation(self) -> Path:
        updater = self.root / "server" / "scripts" / "trainmeet-server-update"
        updater.parent.mkdir(parents=True)
        updater.touch()
        return updater

    def _as_mac(self):
        return (
            patch("tambox_gateway.software_update.sys.platform", "darwin"),
            patch(
                "tambox_gateway.software_update.mac_install_dir",
                return_value=self.root / "server",
            ),
        )

    def test_container_installation_has_no_backend(self) -> None:
        platform, updater = (
            patch("tambox_gateway.software_update.sys.platform", "linux"),
            patch("tambox_gateway.software_update.LINUX_UPDATER", self.root / "missing"),
        )
        with platform, updater:
            self.assertIsNone(update_backend())
            self.assertFalse(supports_updates())

    def test_raspberry_pi_updates_through_systemd(self) -> None:
        installed = self.root / "trainmeet-server-update"
        installed.touch()
        with patch("tambox_gateway.software_update.sys.platform", "linux"), patch(
            "tambox_gateway.software_update.LINUX_UPDATER", installed
        ):
            backend = update_backend()
            with patch("tambox_gateway.software_update.subprocess.run") as run:
                start_update()

        self.assertEqual(backend.kind, "systemd")
        self.assertIn("trainmeet-server-update.service", run.call_args.args[0])

    def test_mac_updates_through_its_own_unprivileged_script(self) -> None:
        updater = self._mac_installation()
        platform, install_dir = self._as_mac()
        with platform, install_dir:
            backend = update_backend()
            with patch("tambox_gateway.software_update.subprocess.Popen") as popen:
                start_update()

        self.assertEqual(backend.kind, "launchd")
        self.assertEqual(popen.call_args.args[0], [str(updater)])
        # The updater restarts the server that spawned it, so it has to outlive
        # its own parent process.
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_unmanaged_installation_refuses_to_update(self) -> None:
        with patch("tambox_gateway.software_update.sys.platform", "linux"), patch(
            "tambox_gateway.software_update.LINUX_UPDATER", self.root / "missing"
        ):
            with self.assertRaisesRegex(SoftwareUpdateError, "uppdateras inte"):
                start_update()

    def test_installed_version_follows_the_active_backend(self) -> None:
        self._mac_installation()
        (self.root / "server" / "VERSION").write_text("abc12345\n", encoding="utf-8")
        platform, install_dir = self._as_mac()
        with platform, install_dir:
            self.assertEqual(installed_version(), "abc12345")


if __name__ == "__main__":
    unittest.main()
