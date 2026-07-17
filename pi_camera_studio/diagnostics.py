from __future__ import annotations

import json
import importlib.util
import platform
import shutil

from .audio import local_audio_summary
from .config import PACKAGE_ROOT, inspect_model


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def collect_diagnostics() -> dict[str, object]:
    try:
        audio = local_audio_summary()
    except Exception as error:  # Diagnostics must survive a broken desktop-audio stack.
        audio = {"capture_available": False, "error": str(error), "sources": []}
    report: dict[str, object] = {
        "application_root": str(PACKAGE_ROOT),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "commands": {name: shutil.which(name) for name in ("ffmpeg", "ffprobe", "arecord")},
        "python_modules": {
            name: _module_available(name)
            for name in ("numpy", "PIL", "PyQt5", "cv2", "libcamera", "picamera2")
        },
        "audio": audio,
        "model": inspect_model().as_dict(),
    }

    try:
        import cv2

        report["opencv"] = {"available": True, "version": cv2.__version__}
    except Exception as error:
        report["opencv"] = {
            "available": False,
            "version": None,
            "error": str(error),
        }

    try:
        from picamera2 import Picamera2
    except Exception as error:
        report["picamera2"] = {"available": False, "error": str(error)}
    else:
        try:
            cameras = Picamera2.global_camera_info()
        except Exception as error:
            report["picamera2"] = {
                "available": True,
                "cameras": None,
                "camera_query_error": str(error),
            }
        else:
            report["picamera2"] = {"available": True, "cameras": cameras}

    return report


def diagnostics_json() -> str:
    return json.dumps(collect_diagnostics(), indent=2, default=str)
