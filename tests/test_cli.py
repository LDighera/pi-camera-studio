from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from pi_camera_studio import __version__


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LightweightCliTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PROJECT_ROOT)
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_main_module_does_not_load_gui_or_camera_dependencies(self) -> None:
        result = self._run_python(
            "import json, sys; import pi_camera_studio.__main__; "
            "print(json.dumps({name: name in sys.modules for name in "
            "['pi_camera_studio.app', 'PyQt5', 'picamera2', 'cv2']}))"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "pi_camera_studio.app": False,
                "PyQt5": False,
                "picamera2": False,
                "cv2": False,
            },
        )

    def test_version_works_with_heavy_imports_blocked(self) -> None:
        result = self._run_python(
            "import builtins, sys; original = builtins.__import__; "
            "blocked = {'PyQt5', 'picamera2', 'cv2', 'numpy'}; "
            "builtins.__import__ = lambda name, *args, **kwargs: "
            "(_ for _ in ()).throw(ImportError(name=name)) "
            "if name.split('.')[0] in blocked else original(name, *args, **kwargs); "
            "from pi_camera_studio.__main__ import main; "
            "sys.argv = ['pi-camera-studio', '--version']; main()"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"pi-camera-studio {__version__}")

    def test_help_works_with_heavy_imports_blocked(self) -> None:
        result = self._run_python(
            "import builtins; original = builtins.__import__; "
            "blocked = {'PyQt5', 'picamera2', 'cv2', 'numpy'}; "
            "builtins.__import__ = lambda name, *args, **kwargs: "
            "(_ for _ in ()).throw(ImportError(name=name)) "
            "if name.split('.')[0] in blocked else original(name, *args, **kwargs); "
            "from pi_camera_studio.cli import main; "
            "raise SystemExit(main(['--help']))"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Integrated Picamera2 camera studio", result.stdout)
        self.assertIn("--diagnose", result.stdout)

    def test_diagnose_reports_missing_heavy_modules_without_traceback(self) -> None:
        result = self._run_python(
            "import sys; "
            "blocked = ['numpy', 'PIL', 'PyQt5', 'cv2', 'libcamera', 'picamera2']; "
            "sys.modules.update({name: None for name in blocked}); "
            "from pi_camera_studio.cli import main; "
            "raise SystemExit(main(['--diagnose']))"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["opencv"]["available"])
        self.assertFalse(report["picamera2"]["available"])
        for module_name in ("numpy", "PIL", "PyQt5", "cv2", "libcamera", "picamera2"):
            self.assertFalse(report["python_modules"][module_name])
        self.assertNotIn("Traceback", result.stderr)

    def test_diagnose_import_error_has_a_clean_cli_message(self) -> None:
        result = self._run_python(
            "import builtins; from pi_camera_studio.cli import main; "
            "original = builtins.__import__; "
            "builtins.__import__ = lambda name, *args, **kwargs: "
            "(_ for _ in ()).throw(ImportError('blocked diagnostics', name=name)) "
            "if name in {'diagnostics', 'pi_camera_studio.diagnostics'} "
            "else original(name, *args, **kwargs); "
            "raise SystemExit(main(['--diagnose']))"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("cannot run diagnostics", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_diagnose_runtime_import_error_has_a_clean_cli_message(self) -> None:
        result = self._run_python(
            "import sys, types; from pi_camera_studio.cli import main; "
            "module = types.ModuleType('pi_camera_studio.diagnostics'); "
            "module.diagnostics_json = lambda: (_ for _ in ()).throw("
            "ImportError('missing diagnostic dependency', name='diagnostic_dependency')); "
            "sys.modules['pi_camera_studio.diagnostics'] = module; "
            "raise SystemExit(main(['--diagnose']))"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("missing Python module 'diagnostic_dependency'", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
