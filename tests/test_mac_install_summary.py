"""Run the real installer's final summary without installing a service."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("zsh"), "Mac installer requires zsh")
class MacInstallSummaryTests(unittest.TestCase):
    def run_summary(self, code=None):
        installer = Path(__file__).resolve().parents[1] / "scripts" / "install-mac.command"
        source = installer.read_text(encoding="utf-8")
        summary = source[source.rindex("\nLOCAL_IP="):]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            if code is not None:
                (state / "connection-code.txt").write_text(code, encoding="utf-8")
            return subprocess.run(
                ["zsh", "-c", "set -euo pipefail\nSTATE_DIR=$1\n"
                 "ipconfig() { print -r -- 192.168.1.50; }\n" + summary,
                 "install-summary", str(state)],
                capture_output=True, text=True, check=False,
            )

    def test_fresh_server_without_connection_code_succeeds(self):
        result = self.run_summary()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TrainMeet Server är installerad", result.stdout)
        self.assertIn("http://192.168.1.50:8787", result.stdout)
        self.assertNotIn("Anslutningskod:", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_existing_connection_code_is_printed(self):
        result = self.run_summary("123-456\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Anslutningskod: 123456", result.stdout)

    def test_empty_connection_code_is_not_printed(self):
        result = self.run_summary("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Anslutningskod:", result.stdout)


if __name__ == "__main__":
    unittest.main()
