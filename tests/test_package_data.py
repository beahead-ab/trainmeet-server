"""Every web file the server serves is in the installed package.

The container on Render and the Raspberry Pi install the server with pip, and
pip only takes the files `pyproject.toml` lists under package-data. Server 4.1.1
added the station sign `web/trainmeet-skylt.svg`, but the list had `web/*.png`
and no `web/*.svg`: locally (run from the source tree) the logo showed, on
server.trainmeet.app it was a broken image in the header (Casper, 2026-10-10).
"""
import fnmatch
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "tmbox_gateway"
# Files beside the pages that are never served: the lock the Data-view
# bundle test reads.
NOT_SERVED = {"web/data-workspace.json"}


def package_data() -> list[str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["setuptools"]["package-data"]["tmbox_gateway"]


def web_files() -> list[str]:
    files = []
    for folder in ("web", "us_web", "terminal16_web"):
        for path in sorted((PACKAGE / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files.append(path.relative_to(PACKAGE).as_posix())
    return files


class PackageDataTests(unittest.TestCase):
    def test_every_web_file_is_installed(self):
        patterns = package_data()
        missing = [name for name in web_files()
                   if name not in NOT_SERVED and not any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)]
        self.assertEqual([], missing, "add them to [tool.setuptools.package-data] in pyproject.toml")

    def test_the_station_sign_is_installed(self):
        """The regression itself: the sign in the header and the login card."""
        self.assertTrue(any(fnmatch.fnmatchcase("web/trainmeet-skylt.svg", pattern) for pattern in package_data()))

    def test_the_exceptions_still_exist(self):
        for name in NOT_SERVED:
            self.assertTrue((PACKAGE / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
