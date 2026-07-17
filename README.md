# Pi Camera Studio

Pi Camera Studio is a local, menu-driven Picamera2 desktop application for
stills, video with optional sound, stop-motion animation, and common-object
detection. Release `0.1.0a1` (`v0.1.0-alpha.1`) is an alpha with a deliberately
narrow hardware target.

## Supported target

The verified development system used:

- Raspberry Pi 5 (`aarch64`)
- Raspberry Pi Camera Module 3 / Sony IMX708, 4608×2592 sensor raster
- Debian GNU/Linux 13 (trixie), arm64, kernel `6.18.34+rpt-rpi-2712`, with the
  Raspberry Pi package repository
- Python 3.13.5, Picamera2 0.3.36, PyQt5 5.15.11, PyAV 14.2, OpenCV 4.10,
  FFmpeg 7.1, and libcamera 0.7.1

Current 64-bit Raspberry Pi OS Desktop is the intended public target because it
provides the required Picamera2/libcamera integration, but it was not separately
verified from the Debian 13 host above. Raspberry Pi 4 and earlier, Compute
Modules, non-IMX708 cameras, 32-bit systems, headless use, X11-only desktops,
and non-Debian distributions are unverified.

A keyboard and mouse are sufficient. Touch is optional; the interface was tuned
for and visually checked with a 7-inch 52Pi EP-0186/ILITEK touchscreen. A USB
microphone or USB audio capture device is optional. The display's 3.5 mm socket,
when present, is normally an output and is not a microphone input.

## Features

- **Still:** full-sensor JPEG or PNG capture, continuous autofocus, manual
  refocus, and exposure compensation.
- **Video + Sound:** 1920×1080, 30 fps H.264 MP4 with optional AAC microphone
  audio. Silent MP4 remains available without an input device.
- **Stop Motion:** named frame sequences, onion-skin preview, delete/retake,
  adjustable playback rate, optional soundtrack, and H.264 MP4 rendering.
- **Object Detection:** local OpenCV NanoDet inference, confidence control,
  labelled overlays, and annotated snapshots for the 80 COCO categories.

Stills and stop-motion frames are fully decoded and checked before their final
names are exposed. Video and rendered stop-motion output is written to a hidden
temporary file first and promoted only after `ffprobe` validates the expected
streams. Failed or interrupted media may be preserved with `.failed` in its
name for inspection.

## Privacy

Pi Camera Studio is local-only. It has no account, telemetry, cloud service,
upload feature, or runtime model download. The NanoDet model is included in the
release at `pi_camera_studio/models/` and is checked before detection is enabled.

The camera is opened and its live preview starts when the GUI opens. Audio
inputs are enumerated at startup, but the application does not record audio
until the user starts a video recording. When an input is found, the first input
is selected and **Record microphone audio (AAC)** is enabled by default; uncheck
it before pressing Record to make a silent video.

Captured media is ordinary, unencrypted user data. The application provides no
access control, automatic deletion, or redaction. Images, sound, diagnostic
output, and object labels can reveal sensitive information about a room or its
occupants; review them before sharing. Object detection does not create a face
database and cannot identify a named person.

`--diagnose` reports local paths, platform information, command locations,
camera inventory, audio devices, and model status. Review that JSON before
posting it publicly. `--hardware-smoke` captures real images and video, may use
the selected microphone, and should be run only in a suitable environment.

## Install system dependencies

Use the operating system's Python packages so Picamera2 and libcamera remain
matched to the Raspberry Pi camera stack:

```sh
sudo apt update
sudo apt install \
  python3-picamera2 python3-pyqt5 python3-av python3-opencv \
  python3-pil python3-numpy ffmpeg alsa-utils pipewire-bin
```

`ffmpeg` supplies both `ffmpeg` and `ffprobe`; `alsa-utils` supplies `arecord`;
and `pipewire-bin` supplies `pw-dump` for desktop microphone discovery. If a
PulseAudio-compatible setup lacks `pw-dump`, `pulseaudio-utils` can optionally
supply `pactl`. `desktop-file-utils` is optional and lets the installer validate
and refresh the desktop entry.

`python3-opencv` is the required Python binding. `libopencv-dev` contains C/C++
headers and build metadata and is not needed merely to run this application.
A pip-only virtual environment is unsupported for this release.

GitHub release assets may also include a Python wheel. The wheel packages the
application, model, and license material, but intentionally does not declare or
install Picamera2, libcamera, PyQt5, PyAV, OpenCV, FFmpeg, or the other operating
system camera-stack dependencies. It is primarily a packaging artifact: the
supported end-user route is to install the `apt` packages above and run
`install.sh`. Distribution packagers may install the wheel into an environment
that can see the operating system's camera stack; an isolated pip-only virtual
environment remains unsupported.

## Install

Clone the alpha tag, or download and extract the corresponding GitHub release:

```sh
git clone --branch v0.1.0-alpha.1 --depth 1 \
  https://github.com/LDighera/pi-camera-studio.git
cd pi-camera-studio
./install.sh
```

Run `install.sh` as the desktop user, not with `sudo`. It verifies the required
Python modules and bundled model, then installs:

- runtime: `${XDG_DATA_HOME:-$HOME/.local/share}/pi-camera-studio`
- terminal launcher: `$HOME/.local/bin/pi-camera-studio`
- desktop entry: `${XDG_DATA_HOME:-$HOME/.local/share}/applications/pi-camera-studio.desktop`

The installed desktop entry contains the current user's absolute launcher path;
the repository template contains no username. If you use a custom
`XDG_DATA_HOME`, it must also be present in the graphical desktop session and
when upgrading or uninstalling.

Open **Pi Camera Studio** from the desktop menu, or run:

```sh
$HOME/.local/bin/pi-camera-studio
```

If `$HOME/.local/bin` is on `PATH`, `pi-camera-studio` works by name. A desktop
menu may require a session restart if its cache does not refresh automatically.

## Upgrade

Check out the desired release tag in a fresh or existing clone and rerun the
installer. For this alpha:

```sh
git fetch --tags
git checkout v0.1.0-alpha.1
./install.sh
```

The installer replaces only its managed runtime, launcher, and desktop entry.
It does not alter captured media. Do not copy a new source tree over the active
runtime by hand. To protect unrelated files, installation and upgrade stop if
any target path already exists without Pi Camera Studio's managed marker.

## Uninstall

From a release checkout:

```sh
./uninstall.sh
```

If the checkout is gone, use the installed copy:

```sh
${XDG_DATA_HOME:-$HOME/.local/share}/pi-camera-studio/uninstall.sh
```

The uninstaller removes only files marked as belonging to the user-local
installation. It leaves system packages and all photographs, videos,
stop-motion sequences, detection snapshots, and failed-media evidence intact.

## Output locations

Unless changed in the interface or through the corresponding environment
variables, output is stored under:

```text
~/Pictures/Pi Camera Studio/Stills
~/Pictures/Pi Camera Studio/Stop Motion
~/Pictures/Pi Camera Studio/Detections
~/Videos/Pi Camera Studio
```

`XDG_PICTURES_DIR` and `XDG_VIDEOS_DIR`, when exported in the application
environment, override the `~/Pictures` and `~/Videos` bases. The directories are
created when the GUI starts.

## Microphone behavior

The application looks for a desktop PipeWire/PulseAudio capture source first and
then enumerates ALSA capture hardware. Use **Refresh Audio Inputs** after
connecting or reconnecting a device. If no input is available, video recording
continues in silent mode.

Always make a short test recording and check the level and perceived A/V sync
before an important session. The alpha verifies that requested audio has
positive duration and a readable AAC packet, but that structural check cannot
judge clipping, room noise, channel selection, or subjective synchronization.

## Object-detection scope

The bundled NanoDet-m-plus-1.5x_416 model detects the standard 80 COCO categories
such as person, bicycle, car, dog, chair, and bottle. It can produce false
positives, miss objects, or draw imperfect boxes. Confidence is not identity.

The feature does **not** provide facial recognition, named-person
identification, tracking of a unique individual, license-plate recognition, or
recognition of arbitrary custom objects. Those uses require different models,
training data, consent considerations, and application logic.

The model is 3,800,954 bytes with SHA-256:

```text
4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186
```

Its pinned source and license are documented in
[`pi_camera_studio/models/model.json`](pi_camera_studio/models/model.json),
[`NOTICE`](NOTICE), and
[`pi_camera_studio/models/LICENSE.NanoDet`](pi_camera_studio/models/LICENSE.NanoDet).

## Diagnostics and command-line options

```sh
pi-camera-studio --version
pi-camera-studio --diagnose
pi-camera-studio --camera 0
pi-camera-studio --windowed
```

The GUI requires a graphical desktop session. Camera index 0 is the default.
Only one process can normally own a camera at a time, so close other camera
applications before launch.

Advanced hardware verification captures real media into the directory supplied
by the user:

```sh
pi-camera-studio --hardware-smoke "$HOME/Pictures/pi-camera-studio-smoke"
```

Do not use that command as a harmless diagnostic: it exercises still capture,
video, optional microphone audio, stop-motion rendering, and detection.

For audio discovery problems, compare the application's diagnostics with
`arecord -l`. On a PipeWire desktop, confirm that `pw-dump` is available. A
Wayland session uses Picamera2's software Qt preview because the tested OpenGL
preview failed with `EGL_BAD_ALLOC`; non-Wayland sessions retain the OpenGL
preview path.

## Known limitations

- This is alpha software with the narrow tested stack described above.
- The application exposes full-resolution stills and a conservative 1080p30
  video mode, not every IMX708 sensor mode or HDR capability.
- Long recordings, disk-full recovery, camera/microphone hot-unplug during an
  operation, suspend/resume, and simultaneous detection plus recording have not
  been comprehensively qualified.
- End-to-end finger gestures on every touchscreen/compositor combination and
  subjective A/V lip-sync remain unproven.
- Stop-motion encoding depends on the codecs available in the distribution's
  FFmpeg build.
- Detection accuracy inherits the limits and biases of the bundled NanoDet/COCO
  model and should not be used for safety-critical decisions.

See [`VERIFICATION.md`](VERIFICATION.md) for the exact evidence boundary.

## Development checks

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
python3 -m pi_camera_studio --diagnose
shellcheck bin/pi-camera-studio deploy/pi-camera-studio install.sh uninstall.sh
desktop-file-validate deploy/pi-camera-studio.desktop
```

The `bin/pi-camera-studio` launcher runs the checkout directly. The installed
launcher runs the copied user-local runtime.

## License

Original Pi Camera Studio code is licensed under the [MIT License](LICENSE).
The bundled NanoDet model and adapted reference code retain their Apache 2.0
terms and attribution; see [NOTICE](NOTICE). No project name or third-party
model name implies endorsement.

Raspberry Pi is a trademark of Raspberry Pi Ltd. Pi Camera Studio is an
independent project for use with compatible Raspberry Pi hardware and is not
affiliated with, authorized by, or endorsed by Raspberry Pi Ltd. This project
does not distribute or use a Raspberry Pi logo.
