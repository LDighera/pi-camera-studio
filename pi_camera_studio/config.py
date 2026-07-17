from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
MODEL_MANIFEST_PATH = PACKAGE_ROOT / "models" / "model.json"
PREVIEW_SIZE = (640, 360)
VIDEO_SIZE = (1920, 1080)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ModelManifestError(RuntimeError):
    """The bundled model manifest is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class ModelManifest:
    """Validated metadata for the model and its bundled license."""

    manifest_path: Path
    name: str
    filename: str
    source_revision: str
    source_url: str
    size: int
    sha256: str
    license_name: str
    license_filename: str
    license_size: int
    license_sha256: str
    classes: str
    input_description: str

    @property
    def model_path(self) -> Path:
        return self.manifest_path.parent / self.filename

    @property
    def license_path(self) -> Path:
        return self.manifest_path.parent / self.license_filename


@dataclass(frozen=True)
class ModelInspection:
    """On-disk state derived exclusively from a model manifest."""

    manifest_path: Path
    manifest: ModelManifest | None
    manifest_error: str | None
    model_size: int | None = None
    model_sha256: str | None = None
    license_size: int | None = None
    license_sha256: str | None = None

    @property
    def model_integrity_ok(self) -> bool:
        return bool(
            self.manifest
            and self.model_size == self.manifest.size
            and self.model_sha256 == self.manifest.sha256
        )

    @property
    def license_integrity_ok(self) -> bool:
        return bool(
            self.manifest
            and self.license_size == self.manifest.license_size
            and self.license_sha256 == self.manifest.license_sha256
        )

    @property
    def integrity_ok(self) -> bool:
        return self.model_integrity_ok and self.license_integrity_ok

    def as_dict(self) -> dict[str, object]:
        manifest = self.manifest
        return {
            "manifest_path": str(self.manifest_path),
            "manifest_valid": manifest is not None,
            "manifest_error": self.manifest_error,
            "name": manifest.name if manifest else None,
            "path": str(manifest.model_path) if manifest else None,
            "present": bool(manifest and manifest.model_path.is_file()),
            "size": self.model_size,
            "expected_size": manifest.size if manifest else None,
            "sha256": self.model_sha256,
            "expected_sha256": manifest.sha256 if manifest else None,
            "model_integrity_ok": self.model_integrity_ok,
            "license_path": str(manifest.license_path) if manifest else None,
            "license_present": bool(manifest and manifest.license_path.is_file()),
            "license_size": self.license_size,
            "license_sha256": self.license_sha256,
            "license_integrity_ok": self.license_integrity_ok,
            "integrity_ok": self.integrity_ok,
        }


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelManifestError(f"Model manifest field {key!r} must be a non-empty string")
    return value.strip()


def _required_filename(data: dict[str, Any], key: str) -> str:
    value = _required_string(data, key)
    if value in {".", ".."} or Path(value).name != value:
        raise ModelManifestError(f"Model manifest field {key!r} must be a plain filename")
    return value


def _required_size(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if type(value) is not int or value <= 0:
        raise ModelManifestError(f"Model manifest field {key!r} must be a positive integer")
    return value


def _required_sha256(data: dict[str, Any], key: str) -> str:
    value = _required_string(data, key).lower()
    if not _SHA256_RE.fullmatch(value):
        raise ModelManifestError(f"Model manifest field {key!r} must be a SHA-256 digest")
    return value


def load_model_manifest(manifest_path: Path = MODEL_MANIFEST_PATH) -> ModelManifest:
    """Load and validate the package-data manifest without hardcoded model metadata."""

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ModelManifestError(f"Model manifest not found: {manifest_path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ModelManifestError(f"Could not read model manifest {manifest_path}: {error}") from error
    if not isinstance(raw, dict):
        raise ModelManifestError("Model manifest must contain a JSON object")
    return ModelManifest(
        manifest_path=manifest_path,
        name=_required_string(raw, "name"),
        filename=_required_filename(raw, "filename"),
        source_revision=_required_string(raw, "source_revision"),
        source_url=_required_string(raw, "source_url"),
        size=_required_size(raw, "bytes"),
        sha256=_required_sha256(raw, "sha256"),
        license_name=_required_string(raw, "license"),
        license_filename=_required_filename(raw, "license_file"),
        license_size=_required_size(raw, "license_bytes"),
        license_sha256=_required_sha256(raw, "license_sha256"),
        classes=_required_string(raw, "classes"),
        input_description=_required_string(raw, "input"),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_model(manifest_path: Path = MODEL_MANIFEST_PATH) -> ModelInspection:
    """Inspect model and license integrity while preserving manifest errors for diagnostics."""

    try:
        manifest = load_model_manifest(manifest_path)
    except ModelManifestError as error:
        return ModelInspection(manifest_path, None, str(error))

    model_size = manifest.model_path.stat().st_size if manifest.model_path.is_file() else None
    model_hash = sha256_file(manifest.model_path) if model_size is not None else None
    license_size = manifest.license_path.stat().st_size if manifest.license_path.is_file() else None
    license_hash = sha256_file(manifest.license_path) if license_size is not None else None
    return ModelInspection(
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_error=None,
        model_size=model_size,
        model_sha256=model_hash,
        license_size=license_size,
        license_sha256=license_hash,
    )


def _xdg_directory(variable: str, fallback_name: str) -> Path:
    value = os.environ.get(variable)
    if value:
        return Path(os.path.expandvars(value)).expanduser()
    return Path.home() / fallback_name


@dataclass(frozen=True)
class CapturePaths:
    root: Path
    stills: Path
    videos: Path
    stop_motion: Path
    detections: Path

    @classmethod
    def for_current_user(cls) -> "CapturePaths":
        pictures = _xdg_directory("XDG_PICTURES_DIR", "Pictures")
        videos = _xdg_directory("XDG_VIDEOS_DIR", "Videos")
        return cls(
            root=pictures / "Pi Camera Studio",
            stills=pictures / "Pi Camera Studio" / "Stills",
            videos=videos / "Pi Camera Studio",
            stop_motion=pictures / "Pi Camera Studio" / "Stop Motion",
            detections=pictures / "Pi Camera Studio" / "Detections",
        )

    def ensure(self) -> None:
        for path in (self.root, self.stills, self.videos, self.stop_motion, self.detections):
            path.mkdir(parents=True, exist_ok=True)


def timestamped_filename(prefix: str, suffix: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return f"{prefix}_{when:%Y%m%d_%H%M%S_%f}{suffix}"
