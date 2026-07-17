"""Console entry point with no camera, OpenCV, or Qt imports at module load time."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
