"""Lightweight command-line routing for Pi Camera Studio."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi-camera-studio", description="Integrated Picamera2 camera studio"
    )
    parser.add_argument(
        "--camera", type=int, default=0, help="Picamera2 camera index (default: 0)"
    )
    parser.add_argument(
        "--diagnose", action="store_true", help="print local diagnostics without opening the GUI"
    )
    parser.add_argument(
        "--smoke-seconds",
        type=float,
        default=0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--smoke-tab",
        type=int,
        choices=range(4),
        default=0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--windowed", action="store_true", help="do not maximize the GUI at startup"
    )
    parser.add_argument(
        "--hardware-smoke",
        type=Path,
        metavar="DIRECTORY",
        help="exercise still, video (with audio when available), stop-motion, and detection",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _missing_dependency_message(error: ImportError, operation: str) -> str:
    dependency = getattr(error, "name", None)
    detail = f"missing Python module {dependency!r}" if dependency else str(error)
    return f"Pi Camera Studio cannot {operation}: {detail}. Run --diagnose for details."


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.diagnose:
        try:
            from .diagnostics import diagnostics_json
            report = diagnostics_json()
        except ImportError as error:
            print(_missing_dependency_message(error, "run diagnostics"), file=sys.stderr)
            return 2

        print(report)
        return 0

    if args.hardware_smoke:
        try:
            from .hardware_smoke import run_hardware_smoke
        except ImportError as error:
            print(_missing_dependency_message(error, "run the hardware smoke test"), file=sys.stderr)
            return 2

        import json

        print(json.dumps(run_hardware_smoke(args.hardware_smoke, args.camera), indent=2))
        return 0

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("Pi Camera Studio requires a graphical desktop session.", file=sys.stderr)
        return 2

    try:
        from .app import launch_gui
    except ImportError as error:
        print(_missing_dependency_message(error, "start the graphical interface"), file=sys.stderr)
        return 2

    return launch_gui(
        camera_number=args.camera,
        windowed=args.windowed,
        smoke_seconds=args.smoke_seconds,
        smoke_tab=args.smoke_tab,
    )
