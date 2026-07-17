from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from pi_camera_studio.config import (
    PACKAGE_ROOT,
    ModelManifestError,
    inspect_model,
    load_model_manifest,
    timestamped_filename,
)


class ConfigTests(unittest.TestCase):
    def test_timestamped_filename_is_stable_and_keeps_suffix(self) -> None:
        when = datetime(2026, 7, 14, 9, 8, 7, 654321)
        self.assertEqual(
            timestamped_filename("still", ".jpg", when),
            "still_20260714_090807_654321.jpg",
        )

    def test_timestamped_filename_adds_missing_dot(self) -> None:
        when = datetime(2026, 7, 14, 9, 8, 7, 0)
        self.assertTrue(timestamped_filename("video", "mp4", when).endswith(".mp4"))

    def test_bundled_manifest_is_authoritative_and_intact(self) -> None:
        inspection = inspect_model()
        self.assertIsNone(inspection.manifest_error)
        self.assertIsNotNone(inspection.manifest)
        self.assertEqual(inspection.manifest.manifest_path.parent, PACKAGE_ROOT / "models")
        self.assertTrue(inspection.model_integrity_ok)
        self.assertTrue(inspection.license_integrity_ok)
        self.assertTrue(inspection.integrity_ok)

    def test_manifest_values_drive_paths_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            model = directory / "custom.onnx"
            license_file = directory / "MODEL-LICENSE"
            model.write_bytes(b"model from manifest")
            license_file.write_bytes(b"license from manifest")
            manifest_path = directory / "model.json"
            data = {
                "name": "Temporary model",
                "filename": model.name,
                "source_revision": "test-revision",
                "source_url": "https://example.invalid/custom.onnx",
                "bytes": model.stat().st_size,
                "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                "license": "Test-only",
                "license_file": license_file.name,
                "license_bytes": license_file.stat().st_size,
                "license_sha256": hashlib.sha256(license_file.read_bytes()).hexdigest(),
                "classes": "test classes",
                "input": "1x1 RGB",
            }
            manifest_path.write_text(json.dumps(data), encoding="utf-8")

            manifest = load_model_manifest(manifest_path)
            self.assertEqual(manifest.model_path, model)
            self.assertEqual(manifest.size, len(b"model from manifest"))
            self.assertTrue(inspect_model(manifest_path).integrity_ok)

            data["bytes"] += 1
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            self.assertFalse(inspect_model(manifest_path).model_integrity_ok)

    def test_manifest_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "model.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "name": "Unsafe model",
                        "filename": "../outside.onnx",
                        "source_revision": "test",
                        "source_url": "https://example.invalid/model",
                        "bytes": 1,
                        "sha256": "0" * 64,
                        "license": "Test",
                        "license_file": "LICENSE",
                        "license_bytes": 1,
                        "license_sha256": "0" * 64,
                        "classes": "test",
                        "input": "test",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ModelManifestError):
                load_model_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()
