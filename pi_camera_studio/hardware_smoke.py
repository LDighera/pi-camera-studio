from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

import cv2
from PIL import Image
from libcamera import controls
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput

from .audio import detect_audio_sources
from .config import PREVIEW_SIZE, VIDEO_SIZE, inspect_model
from .detector import NanoDetDetector, annotate_frame
from .media import build_stop_motion_ffmpeg_args, probe_media_file, rollback_partial_encoder


def _image_report(path: Path) -> dict[str, object]:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return {"path": str(path), "format": image.format, "size": list(image.size), "bytes": path.stat().st_size}


def run_hardware_smoke(parent: Path, camera_number: int = 0) -> dict[str, object]:
    """Exercise real camera modes, including AAC audio when a capture source exists."""
    model_inspection = inspect_model()
    if not model_inspection.integrity_ok or model_inspection.manifest is None:
        detail = model_inspection.manifest_error or "model or license integrity check failed"
        raise RuntimeError(f"Object-detection model is unavailable: {detail}")
    model_path = model_inspection.manifest.model_path
    run_directory = parent / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    frames_directory = run_directory / "stop_frames"
    frames_directory.mkdir(parents=True, exist_ok=False)

    still_path = run_directory / "still.jpg"
    video_temp = run_directory / ".video.recording.mp4"
    video_path = run_directory / "video.mp4"
    detection_path = run_directory / "detection.jpg"
    stop_video_path = run_directory / "stop-motion.mp4"

    camera = Picamera2(camera_number)
    video_config = camera.create_video_configuration(
        main={"size": VIDEO_SIZE, "format": "YUV420"},
        lores={"size": PREVIEW_SIZE, "format": "RGB888"},
        raw=None,
        display="lores",
        encode="main",
        buffer_count=6,
        queue=True,
        controls={"FrameRate": 30.0},
    )
    still_config = camera.create_still_configuration(
        main={"size": camera.sensor_resolution, "format": "RGB888"},
        raw=None,
        display="main",
        buffer_count=3,
        queue=False,
    )
    audio_sources = detect_audio_sources()
    audio_source = audio_sources[0] if audio_sources else None
    report: dict[str, object] = {
        "run_directory": str(run_directory),
        "camera_model": camera.camera_properties.get("Model"),
        "sensor_resolution": list(camera.sensor_resolution),
        "audio_tested": bool(audio_source),
        "audio_source": audio_source.label if audio_source else None,
        "audio_reason": None
        if audio_source
        else "No ALSA or PipeWire capture source is exposed to the Pi",
    }

    camera.configure(video_config)
    camera.start()
    try:
        try:
            camera.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        except Exception:
            pass
        time.sleep(1.5)

        frame = camera.capture_array("lores")
        detector = NanoDetDetector(model_path)
        detection = detector.detect(frame)
        annotated = annotate_frame(frame, detection.detections)
        if not cv2.imwrite(str(detection_path), annotated):
            raise RuntimeError("Could not write the real-camera detection image")
        report["detection"] = {
            "path": str(detection_path),
            "bytes": detection_path.stat().st_size,
            "inference_ms": round(detection.inference_ms, 2),
            "objects": [
                {"label": item.label, "confidence": round(item.confidence, 4)}
                for item in detection.detections
            ],
        }

        encoder = H264Encoder(bitrate=12_000_000, repeat=True, framerate=30)
        if audio_source:
            encoder.audio = True
            encoder.audio_input = audio_source.pyav_open_kwargs()
            encoder.audio_output = {"codec_name": "aac"}
            encoder.audio_sync = -100_000
        output = PyavOutput(str(video_temp), format="mp4")
        mux_errors: list[str] = []
        output.error_callback = lambda error: mux_errors.append(str(error))
        encoder_started = False
        try:
            camera.start_encoder(encoder, output, name="main")
            encoder_started = True
            time.sleep(3.0)
            camera.stop_encoder(encoder)
            encoder_started = False
            if mux_errors:
                raise RuntimeError(f"Video muxing failed: {mux_errors[0]}")
        except Exception as error:
            if encoder_started:
                try:
                    camera.stop_encoder(encoder)
                except Exception:
                    camera.encoders.discard(encoder)
            rollback_errors = rollback_partial_encoder(encoder, output)
            failed_video = run_directory / "video.failed.mp4"
            if video_temp.is_file() and video_temp.stat().st_size > 0:
                video_temp.replace(failed_video)
            detail = str(error)
            if rollback_errors:
                detail += "; " + "; ".join(rollback_errors)
            if failed_video.is_file():
                detail += f"; incomplete recording: {failed_video}"
            raise RuntimeError(detail) from error
        video_probe = probe_media_file(video_temp, require_audio=bool(audio_source))
        video_temp.replace(video_path)
        report["video"] = {
            "path": str(video_path),
            "bytes": video_path.stat().st_size,
            "streams": [stream.get("codec_type") for stream in video_probe["streams"]],
            "stream_details": [
                {
                    "type": stream.get("codec_type"),
                    "codec": stream.get("codec_name"),
                    "duration": stream.get("duration"),
                }
                for stream in video_probe["streams"]
            ],
            "duration": video_probe["format"].get("duration"),
        }

        camera.switch_mode_and_capture_file(still_config, str(still_path), name="main", delay=1)
        report["still"] = _image_report(still_path)

        for index in (1, 2):
            frame_path = frames_directory / f"frame_{index:06d}.jpg"
            camera.switch_mode_and_capture_file(still_config, str(frame_path), name="main", delay=1)
        report["stop_frames"] = [_image_report(path) for path in sorted(frames_directory.glob("frame_*.jpg"))]
    finally:
        try:
            camera.stop()
        except Exception:
            pass
        camera.close()

    ffmpeg_args = build_stop_motion_ffmpeg_args(
        frames_directory, stop_video_path, fps=2, soundtrack=None, frame_count=2
    )
    subprocess.run(["ffmpeg", *ffmpeg_args], check=True)
    stop_probe = probe_media_file(stop_video_path, require_audio=False)
    report["stop_video"] = {
        "path": str(stop_video_path),
        "bytes": stop_video_path.stat().st_size,
        "streams": [stream.get("codec_type") for stream in stop_probe["streams"]],
        "duration": stop_probe["format"].get("duration"),
    }

    report_path = run_directory / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report
