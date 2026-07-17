from __future__ import annotations

import unittest

import numpy as np

from pi_camera_studio.config import load_model_manifest
from pi_camera_studio.detector import (
    Detection,
    NanoDetDetector,
    cv2,
    letterbox,
    render_overlay,
    unletterbox_box,
)


BUNDLED_MODEL_PATH = load_model_manifest().model_path


@unittest.skipUnless(cv2 is not None, "python3-opencv is not installed")
class DetectorGeometryTests(unittest.TestCase):
    def test_letterbox_and_inverse_mapping(self) -> None:
        image = np.zeros((360, 640, 3), dtype=np.uint8)
        boxed, geometry = letterbox(image)
        self.assertEqual(boxed.shape, (416, 416, 3))
        mapped = unletterbox_box(np.array([0, 91, 415, 324]), image.shape[:2], geometry)
        self.assertLessEqual(mapped[0], 1)
        self.assertLessEqual(mapped[1], 1)
        self.assertGreaterEqual(mapped[2], 638)
        self.assertGreaterEqual(mapped[3], 358)

    def test_overlay_is_rgba_and_nonempty(self) -> None:
        detection = Detection(10, 10, 100, 80, 0.9, 0)
        overlay = render_overlay((640, 360), (detection,))
        self.assertEqual(overlay.shape, (360, 640, 4))
        self.assertGreater(int(overlay[:, :, 3].max()), 0)

    def test_pinned_model_loads_and_infers(self) -> None:
        self.assertTrue(BUNDLED_MODEL_PATH.is_file(), "bundled NanoDet model is missing")
        detector = NanoDetDetector(BUNDLED_MODEL_PATH)
        result = detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
        self.assertEqual(result.frame_size, (640, 360))
        self.assertGreater(result.inference_ms, 0)


if __name__ == "__main__":
    unittest.main()
