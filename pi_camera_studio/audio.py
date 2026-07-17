from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


_ALSA_CAPTURE_RE = re.compile(
    r"^card\s+(?P<card>\d+):.*?\[(?P<card_name>[^\]]+)\],\s*"
    r"device\s+(?P<device>\d+):.*?\[(?P<device_name>[^\]]+)\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AudioSource:
    label: str
    input_format: str
    device: str

    def pyav_open_kwargs(self) -> dict[str, str]:
        return {"file": self.device, "format": self.input_format}


def parse_arecord_devices(output: str) -> list[AudioSource]:
    sources: list[AudioSource] = []
    seen: set[tuple[int, int]] = set()
    for raw_line in output.splitlines():
        match = _ALSA_CAPTURE_RE.search(raw_line.strip())
        if not match:
            continue
        card = int(match.group("card"))
        device = int(match.group("device"))
        key = (card, device)
        if key in seen:
            continue
        seen.add(key)
        card_name = match.group("card_name").strip().rstrip(",")
        device_name = match.group("device_name").strip().rstrip(",")
        sources.append(
            AudioSource(
                label=f"{card_name} — {device_name} (card {card}, device {device})",
                input_format="alsa",
                device=f"hw:{card},{device}",
            )
        )
    return sources


def _pulse_has_capture_source() -> bool:
    pactl = shutil.which("pactl")
    if not pactl:
        return False
    try:
        result = subprocess.run(
            [pactl, "list", "short", "sources"],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return any(line and ".monitor" not in line for line in result.stdout.splitlines())


def _pipewire_has_capture_source() -> bool:
    pw_dump = shutil.which("pw-dump")
    if not pw_dump:
        return False
    try:
        result = subprocess.run(
            [pw_dump], text=True, capture_output=True, timeout=4, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        objects = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    for item in objects:
        properties = item.get("info", {}).get("props", {})
        if properties.get("media.class") == "Audio/Source":
            name = str(properties.get("node.name", ""))
            if ".monitor" not in name:
                return True
    return False


def _pulse_socket_available() -> bool:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return bool(runtime and (Path(runtime) / "pulse" / "native").exists())


def detect_audio_sources() -> list[AudioSource]:
    sources: list[AudioSource] = []
    if (_pulse_has_capture_source() or _pipewire_has_capture_source()) and _pulse_socket_available():
        sources.append(AudioSource("Default desktop audio input", "pulse", "default"))

    arecord = shutil.which("arecord")
    if arecord:
        try:
            result = subprocess.run(
                [arecord, "-l"],
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result and result.returncode == 0:
            sources.extend(parse_arecord_devices(result.stdout))

    unique: list[AudioSource] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (source.input_format, source.device)
        if key not in seen:
            unique.append(source)
            seen.add(key)
    return unique


def local_audio_summary() -> dict[str, object]:
    sources = detect_audio_sources()
    return {
        "capture_available": bool(sources),
        "sources": [
            {"label": source.label, "format": source.input_format, "device": source.device}
            for source in sources
        ],
        "alsa_pcm_inventory": Path("/proc/asound/pcm").read_text(errors="replace")
        if Path("/proc/asound/pcm").exists()
        else None,
    }
