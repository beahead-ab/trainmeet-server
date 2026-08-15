from __future__ import annotations

import unittest
from unittest.mock import patch

from tambox_gateway.software_update import SoftwareUpdateError, latest_version


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
    def test_test_channel_reads_commit_from_public_patch(self, open_url) -> None:
        open_url.return_value = _Response(
            b"From 3aec36552bfb15883cb30b70db19f3152466fc3f Mon Sep 17 00:00:00 2001\n",
            "https://github.com/beahead-ab/trainmeet-server/commit/main.patch",
        )

        result = latest_version("test")

        self.assertEqual(result["version"], "3aec3655")
        self.assertIn("/commit/main.patch", open_url.call_args.args[0].full_url)

    @patch("tambox_gateway.software_update.urlopen")
    def test_stable_channel_reads_tag_from_redirect(self, open_url) -> None:
        open_url.return_value = _Response(
            b"",
            "https://github.com/beahead-ab/trainmeet-server/releases/tag/v1.2.3",
        )

        result = latest_version("stable")

        self.assertEqual(result["version"], "v1.2.3")

    @patch("tambox_gateway.software_update.urlopen")
    def test_invalid_patch_is_reported_clearly(self, open_url) -> None:
        open_url.return_value = _Response(
            b"not a commit patch",
            "https://github.com/beahead-ab/trainmeet-server/commit/main.patch",
        )

        with self.assertRaisesRegex(SoftwareUpdateError, "giltig versionsinformation"):
            latest_version("test")


if __name__ == "__main__":
    unittest.main()
