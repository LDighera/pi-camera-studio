# Contributing to Pi Camera Studio

Thank you for helping improve Pi Camera Studio. The initial alpha has a narrow
verified configuration: Debian 13 arm64 on a Raspberry Pi 5, using Raspberry
Pi's camera stack and a Camera Module 3. Current 64-bit Raspberry Pi OS Desktop
is the intended public target, but it has not yet been separately verified.
Reports from other operating systems or hardware are useful, but should identify
that configuration as experimental.

## Before opening an issue

- Search existing issues for the same behavior.
- Run `python3 -m pi_camera_studio --diagnose` and remove personal paths or
  device names before sharing its output.
- Include the Raspberry Pi model, Raspberry Pi OS release, camera module,
  display session (Wayland or X11), and microphone type when relevant.
- For a defect, include concise reproduction steps and the expected and actual
  behavior. Do not attach private photographs, recordings, or credentials.

Security-sensitive reports belong in a private GitHub security advisory, as
described in `SECURITY.md`, rather than a public issue.

## Development setup

Use the Raspberry Pi OS runtime packages listed in `README.md`; the camera stack
is not supported inside an isolated pip-managed environment. Install the release
checks with
`sudo apt install desktop-file-utils python3-build python3-venv shellcheck`.
The release gate builds without PEP 517 isolation, so its active Python must
provide Setuptools 77 or newer as declared in `pyproject.toml`.

If the operating system supplies an older Setuptools, prepare the same
system-package-aware build environment used by CI outside the source checkout:

```sh
python3 -m venv --system-site-packages /tmp/pi-camera-studio-source-tests
/tmp/pi-camera-studio-source-tests/bin/python -m pip install setuptools==78.1.1
PATH="/tmp/pi-camera-studio-source-tests/bin:$PATH" scripts/release-check
```

Otherwise, run from a source checkout:

```sh
scripts/release-check
```

That gate compiles the sources, runs the complete unit suite, exercises the
bundled detector model, checks every shell script and desktop entry, and builds
and inspects both distribution formats. Run it from a clean checkout; it rejects
Python bytecode and cache directories rather than silently packaging them.

The unit suite must exercise the bundled detector model. A skipped NanoDet test
is not a successful release check. Hardware changes also require the documented
hardware smoke test on a supported Raspberry Pi, but contributors should inspect
all generated media before sharing it.

## Pull requests

- Keep each change focused and explain its user-visible effect.
- Add or update tests for behavior that can be tested without camera hardware.
- Update documentation and `CHANGELOG.md` when behavior or compatibility changes.
- Preserve model attribution and integrity metadata when touching detector assets.
- Do not commit generated media, bytecode, virtual environments, or build output.

By submitting a contribution, you agree that it may be distributed under this
project's MIT License.
