from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_FRAME_RE = re.compile(r"^frame_(\d{6})\.jpg$")


def safe_session_name(name: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", name.strip()).strip("._-")
    return cleaned[:80] or f"sequence_{datetime.now():%Y%m%d_%H%M%S}"


@dataclass
class StopMotionSession:
    directory: Path
    fps: int = 12

    @classmethod
    def create(cls, parent: Path, name: str, fps: int = 12) -> "StopMotionSession":
        directory = parent / safe_session_name(name)
        directory.mkdir(parents=True, exist_ok=False)
        session = cls(directory=directory, fps=fps)
        session.write_manifest()
        return session

    @classmethod
    def open(cls, directory: Path) -> "StopMotionSession":
        if not directory.is_dir():
            raise RuntimeError(f"Sequence directory does not exist: {directory}")
        manifest_path = directory / "session.json"
        fps = 12
        if manifest_path.exists():
            try:
                manifest_text = manifest_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise RuntimeError(
                    f"Could not read stop-motion manifest {manifest_path}: {error}"
                ) from error
            try:
                data = json.loads(manifest_text)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"Malformed stop-motion manifest {manifest_path}: "
                    f"{error.msg} at line {error.lineno}, column {error.colno}"
                ) from error
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"Malformed stop-motion manifest {manifest_path}: "
                    "the top-level value must be an object"
                )
            try:
                fps = int(data.get("fps", fps))
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    f"Invalid stop-motion frame rate in {manifest_path}: "
                    f"{data.get('fps')!r}"
                ) from error
        if fps < 1 or fps > 120:
            raise RuntimeError(f"Invalid stop-motion frame rate in {manifest_path}: {fps}")
        session = cls(directory=directory, fps=fps)
        session.validate_contiguous_frames()
        return session

    @property
    def manifest_path(self) -> Path:
        return self.directory / "session.json"

    def frame_paths(self) -> list[Path]:
        return sorted(
            path for path in self.directory.iterdir() if path.is_file() and _FRAME_RE.match(path.name)
        )

    def validate_contiguous_frames(self, minimum: int = 0) -> list[Path]:
        frames = self.frame_paths()
        if len(frames) < minimum:
            raise RuntimeError(f"Sequence needs at least {minimum} frames")
        for expected_number, path in enumerate(frames, start=1):
            expected_name = f"frame_{expected_number:06d}.jpg"
            if path.name != expected_name:
                raise RuntimeError(
                    f"Frame sequence is not contiguous: expected {expected_name}, "
                    f"found {path.name}"
                )
        return frames

    @property
    def frame_count(self) -> int:
        return len(self.frame_paths())

    def next_frame_path(self) -> Path:
        existing = self.frame_paths()
        next_number = 1
        if existing:
            match = _FRAME_RE.match(existing[-1].name)
            if match:
                next_number = int(match.group(1)) + 1
        return self.directory / f"frame_{next_number:06d}.jpg"

    def last_frame_path(self) -> Path | None:
        frames = self.frame_paths()
        return frames[-1] if frames else None

    def delete_last_frame(self) -> Path | None:
        path = self.last_frame_path()
        if path:
            path.unlink()
            self.write_manifest()
        return path

    def write_manifest(self) -> None:
        data = {
            "format": 1,
            "fps": int(self.fps),
            "frame_count": self.frame_count,
            "frame_pattern": "frame_%06d.jpg",
            "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        payload = json.dumps(data, indent=2) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=".session.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(self.manifest_path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def build_stop_motion_ffmpeg_args(
    frames_directory: Path,
    output_path: Path,
    fps: int,
    soundtrack: Path | None = None,
    frame_count: int | None = None,
) -> list[str]:
    if fps < 1 or fps > 120:
        raise ValueError("Stop-motion frame rate must be between 1 and 120 fps")
    args = [
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-framerate",
        str(fps),
        "-start_number",
        "1",
        "-i",
        str(frames_directory / "frame_%06d.jpg"),
    ]
    if soundtrack:
        args.extend(["-stream_loop", "-1", "-i", str(soundtrack)])
    args.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"])
    if soundtrack:
        args.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])
    if frame_count is not None:
        if frame_count < 1:
            raise ValueError("Stop-motion frame count must be positive")
        args.extend(["-t", f"{frame_count / fps:.6f}"])
    args.extend(["-movflags", "+faststart", str(output_path)])
    return args


def probe_media_file(path: Path, require_audio: bool = False) -> dict[str, object]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is not installed")
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(path),
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"ffprobe timed out for {path}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"ffprobe rejected {path}: {detail}")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("ffprobe returned invalid JSON") from error
    streams = report.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video_streams:
        raise RuntimeError("The output contains no video stream")
    if require_audio and not audio_streams:
        raise RuntimeError("Audio was requested, but the output contains no audio stream")
    if require_audio:
        try:
            video_duration = float(video_streams[0].get("duration", 0))
            audio_duration = float(audio_streams[0].get("duration", 0))
        except (TypeError, ValueError) as error:
            raise RuntimeError("The output has no usable A/V duration metadata") from error
        if video_duration <= 0 or audio_duration <= 0:
            raise RuntimeError("The output contains a zero-duration audio or video stream")
        if audio_duration < max(0.1, video_duration * 0.8):
            raise RuntimeError(
                f"Audio is too short ({audio_duration:.3f}s for {video_duration:.3f}s of video)"
            )
        try:
            packet_probe = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-read_intervals",
                    "%+#1",
                    "-show_entries",
                    "packet=size",
                    "-of",
                    "json",
                    str(path),
                ],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("ffprobe timed out while checking audio packets") from error
        try:
            packet_report = json.loads(packet_probe.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("ffprobe returned invalid audio-packet JSON") from error
        packets = packet_report.get("packets", []) if packet_probe.returncode == 0 else []
        try:
            packet_size = int(packets[0].get("size", 0)) if packets else 0
        except (TypeError, ValueError):
            packet_size = 0
        if packet_size <= 0:
            raise RuntimeError("The audio stream contains no readable packet")
    return report


class _Picamera2PartialStartCleanup:
    """Compatibility boundary for Picamera2/PyAV partial-start internals.

    Picamera2 currently has no public abort-start API. Audio-device failures can
    occur after its running flag and PyAV containers are created but before its
    audio thread exists. Keep all version-sensitive attributes isolated here so
    the normal recording path uses only Picamera2's public API.
    """

    _CONTAINER_ATTRIBUTES = (
        "_audio_input_container",
        "_audio_output_container",
        "_container",
    )

    def __init__(self, encoder):
        self.encoder = encoder

    def rollback(self, errors: list[str]) -> None:
        private_cleanup_needed = self._disable_unstarted_audio(errors)
        if bool(getattr(self.encoder, "running", False)):
            try:
                self.encoder.stop()
            except Exception as error:
                errors.append(f"encoder rollback: {error}")
                private_cleanup_needed = True
        if bool(getattr(self.encoder, "running", False)):
            private_cleanup_needed = True
        if private_cleanup_needed:
            self._close_private_containers(errors)
        self._clear_stale_running_flag(errors)

    def _disable_unstarted_audio(self, errors: list[str]) -> bool:
        audio_thread = getattr(self.encoder, "_audio_thread", None)
        if not bool(getattr(self.encoder, "audio", False)):
            return False
        if audio_thread is not None and getattr(audio_thread, "ident", None) is not None:
            return False
        try:
            self.encoder.audio = False
        except Exception as error:
            errors.append(f"disable partial audio: {error}")
        return True

    def _close_private_containers(self, errors: list[str]) -> None:
        for attribute in self._CONTAINER_ATTRIBUTES:
            container = getattr(self.encoder, attribute, None)
            if container is None:
                continue
            try:
                container.close()
            except Exception as error:
                errors.append(f"{attribute} close: {error}")

    def _clear_stale_running_flag(self, errors: list[str]) -> None:
        if not bool(getattr(self.encoder, "running", False)):
            return
        try:
            setattr(self.encoder, "_running", False)
        except Exception as error:
            errors.append(f"clear encoder running state: {error}")


def rollback_partial_encoder(encoder, output) -> list[str]:
    """Undo Picamera2 state when audio-device setup fails partway through start()."""

    errors: list[str] = []
    if encoder is not None:
        _Picamera2PartialStartCleanup(encoder).rollback(errors)
    if output is not None:
        try:
            output.stop()
        except Exception as error:
            errors.append(f"output rollback: {error}")
    return errors


def validate_image_file(path: Path, expected_size: tuple[int, int]) -> dict[str, object]:
    """Fully decode an image before it is promoted from a temporary capture."""
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("The camera created no usable image file")
    try:
        with Image.open(path) as image:
            image_format = image.format
            image_size = image.size
            image.verify()
        with Image.open(path) as image:
            image.load()
    except Exception as error:
        raise RuntimeError(f"Image validation failed: {error}") from error
    if image_size != expected_size:
        raise RuntimeError(
            f"Image size is {image_size[0]}x{image_size[1]}, expected "
            f"{expected_size[0]}x{expected_size[1]}"
        )
    expected_format = "PNG" if path.suffix.lower() == ".png" else "JPEG"
    if image_format != expected_format:
        raise RuntimeError(f"Image format is {image_format}, expected {expected_format}")
    return {
        "format": image_format,
        "size": image_size,
        "bytes": path.stat().st_size,
    }
