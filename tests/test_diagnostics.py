from __future__ import annotations

import sys
import unittest
from unittest import mock

from pi_camera_studio.diagnostics import collect_diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_report_survives_missing_optional_opencv_and_picamera(self) -> None:
        audio = {"capture_available": False, "sources": []}
        with mock.patch.dict(sys.modules, {"cv2": None, "picamera2": None}):
            with mock.patch(
                "pi_camera_studio.diagnostics.local_audio_summary", return_value=audio
            ):
                report = collect_diagnostics()

        self.assertFalse(report["opencv"]["available"])
        self.assertFalse(report["picamera2"]["available"])
        self.assertEqual(report["audio"], audio)
        self.assertTrue(report["model"]["manifest_valid"])
        self.assertTrue(report["model"]["integrity_ok"])

    def test_report_contains_audio_error_instead_of_raising(self) -> None:
        with mock.patch(
            "pi_camera_studio.diagnostics.local_audio_summary",
            side_effect=RuntimeError("audio service unavailable"),
        ):
            report = collect_diagnostics()
        self.assertIn("audio service unavailable", report["audio"]["error"])


if __name__ == "__main__":
    unittest.main()
