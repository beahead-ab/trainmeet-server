"""The standalone production lab must include every imported dependency."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class LabPackageTests(unittest.TestCase):
    def test_delivery_file_set_imports_and_renders_without_source_checkout(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("lab_packager", root / "deploy/terminal16/package.py")
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        with TemporaryDirectory() as directory:
            target = Path(directory)
            for relative in packager.FILES:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / relative, destination)
            code = """
import sys
sys.path.insert(0, sys.argv[1])
from tmbox_gateway.terminal16_public import PublicLabServer
from tmbox_gateway.terminal16_demo import demo_lab
lab = demo_lab()
frames = lab.frames()
assert len(frames) == 3
assert all(frame['active']['count'] == 0 for frame in frames)
assert all(list(map(len, frame['lines'])) == [16, 16] for frame in frames)
"""
            result = subprocess.run([sys.executable, "-I", "-c", code, str(target / "src")],
                                    cwd=target, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
