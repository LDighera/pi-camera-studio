from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from pi_camera_studio.media import (
    StopMotionSession,
    build_stop_motion_ffmpeg_args,
    rollback_partial_encoder,
    safe_session_name,
    validate_image_file,
)


class StopMotionTests(unittest.TestCase):
    def test_safe_session_name(self) -> None:
        self.assertEqual(safe_session_name("  My first scene!  "), "My_first_scene")

    def test_session_frame_sequence_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            session = StopMotionSession.create(parent, "scene", fps=15)
            self.assertEqual(session.next_frame_path().name, "frame_000001.jpg")
            session.next_frame_path().write_bytes(b"frame one")
            self.assertEqual(session.next_frame_path().name, "frame_000002.jpg")
            session.next_frame_path().write_bytes(b"frame two")
            session.write_manifest()
            manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["frame_count"], 2)
            self.assertEqual(manifest["fps"], 15)
            deleted = session.delete_last_frame()
            self.assertEqual(deleted.name, "frame_000002.jpg")
            self.assertEqual(session.frame_count, 1)

    def test_manifest_write_is_atomic_and_cleans_failed_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = StopMotionSession.create(Path(temporary), "scene", fps=12)
            original_manifest = session.manifest_path.read_bytes()
            session.fps = 24

            with mock.patch.object(
                Path, "replace", side_effect=OSError("simulated replace failure")
            ):
                with self.assertRaisesRegex(OSError, "simulated replace failure"):
                    session.write_manifest()

            self.assertEqual(session.manifest_path.read_bytes(), original_manifest)
            self.assertEqual(list(session.directory.glob(".session.*.tmp")), [])

    def test_open_rejects_malformed_manifest_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "scene"
            directory.mkdir()
            manifest = directory / "session.json"
            manifest.write_text('{"fps": 12, broken', encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeError, r"Malformed stop-motion manifest .*session\.json"
            ):
                StopMotionSession.open(directory)

    def test_open_rejects_non_object_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "scene"
            directory.mkdir()
            (directory / "session.json").write_text("[]\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "top-level value must be an object"):
                StopMotionSession.open(directory)

    def test_gapped_sequence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "scene"
            directory.mkdir()
            (directory / "frame_000001.jpg").write_bytes(b"first")
            (directory / "frame_000003.jpg").write_bytes(b"third")
            with self.assertRaises(RuntimeError):
                StopMotionSession.open(directory)

    def test_ffmpeg_arguments_with_soundtrack(self) -> None:
        args = build_stop_motion_ffmpeg_args(
            Path("/tmp/frames"), Path("/tmp/movie.mp4"), 12, Path("/tmp/music.wav"), 24
        )
        self.assertIn("/tmp/frames/frame_%06d.jpg", args)
        self.assertIn("-shortest", args)
        self.assertEqual(args[args.index("-t") + 1], "2.000000")
        self.assertEqual(args[-1], "/tmp/movie.mp4")

    def test_invalid_frame_rate_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_stop_motion_ffmpeg_args(Path("/tmp"), Path("/tmp/x.mp4"), 0)

    def test_image_validation_decodes_and_checks_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".capture.jpg"
            Image.new("RGB", (64, 48), "navy").save(path, format="JPEG")
            report = validate_image_file(path, (64, 48))
            self.assertEqual(report["format"], "JPEG")
            self.assertEqual(report["size"], (64, 48))
            with self.assertRaises(RuntimeError):
                validate_image_file(path, (48, 64))

    def test_image_validation_rejects_truncated_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".capture.jpg"
            Image.new("RGB", (64, 48), "navy").save(path, format="JPEG")
            path.write_bytes(path.read_bytes()[:40])
            with self.assertRaises(RuntimeError):
                validate_image_file(path, (64, 48))

    def test_partial_encoder_rollback_disables_unstarted_audio(self) -> None:
        class FakeEncoder:
            audio = True
            running = True

            def stop(self) -> None:
                if self.audio:
                    raise AttributeError("audio thread was never created")
                self.running = False

        class FakeOutput:
            recording = True

            def stop(self) -> None:
                self.recording = False

        encoder = FakeEncoder()
        output = FakeOutput()
        self.assertEqual(rollback_partial_encoder(encoder, output), [])
        self.assertFalse(encoder.running)
        self.assertFalse(output.recording)

    def test_partial_encoder_rollback_contains_private_compatibility_cleanup(self) -> None:
        class FakeContainer:
            closed = False

            def close(self) -> None:
                self.closed = True

        class FakeEncoder:
            audio = False
            _running = True
            _audio_input_container = FakeContainer()
            _audio_output_container = FakeContainer()
            _container = FakeContainer()

            @property
            def running(self) -> bool:
                return self._running

            def stop(self) -> None:
                raise RuntimeError("partial start cannot use public stop")

        class FakeOutput:
            stopped = False

            def stop(self) -> None:
                self.stopped = True

        encoder = FakeEncoder()
        output = FakeOutput()
        errors = rollback_partial_encoder(encoder, output)
        self.assertTrue(any("encoder rollback" in error for error in errors))
        self.assertFalse(encoder.running)
        self.assertTrue(encoder._audio_input_container.closed)
        self.assertTrue(encoder._audio_output_container.closed)
        self.assertTrue(encoder._container.closed)
        self.assertTrue(output.stopped)

    def test_normal_audio_rollback_does_not_repeat_private_cleanup(self) -> None:
        class FakeThread:
            ident = 123

        class FakeContainer:
            close_count = 0

            def close(self) -> None:
                self.close_count += 1

        class FakeEncoder:
            audio = True
            running = True
            _audio_thread = FakeThread()
            _audio_input_container = FakeContainer()
            _audio_output_container = FakeContainer()

            def stop(self) -> None:
                self.running = False
                self._audio_input_container.close()
                self._audio_output_container.close()

        class FakeOutput:
            def stop(self) -> None:
                pass

        encoder = FakeEncoder()
        self.assertEqual(rollback_partial_encoder(encoder, FakeOutput()), [])
        self.assertEqual(encoder._audio_input_container.close_count, 1)
        self.assertEqual(encoder._audio_output_container.close_count, 1)


if __name__ == "__main__":
    unittest.main()
