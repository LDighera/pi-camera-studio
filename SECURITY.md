# Security policy

## Supported versions

Pi Camera Studio is currently an alpha project. Security fixes are applied to
the latest `0.1.x` alpha release and the current default branch. Older snapshots
are not supported.

## Reporting a vulnerability

Please use GitHub's **Security** tab to open a private security advisory for the
repository. Include the affected version, impact, reproduction details, and any
suggested mitigation. Do not include private photographs, recordings, device
identifiers, credentials, or other unrelated personal data.

Use a public issue only for reports that have no sensitive or exploitable
details. Please allow maintainers time to reproduce and address a vulnerability
before public disclosure.

## Scope and dependencies

Pi Camera Studio processes camera, microphone, image, and media files locally.
Normal startup does not download models or send captures to a service. The
bundled object-detection model is checked against pinned size and SHA-256 values.

Picamera2, libcamera, Qt, OpenCV, PyAV, FFmpeg, Pillow, ALSA, and related system
components are supplied by Raspberry Pi OS. Vulnerabilities in those components
should also be reported to their upstream project or Raspberry Pi OS as
appropriate. Reports about unsafe invocation, path handling, malformed media,
permissions, or unexpected disclosure within Pi Camera Studio remain in scope
for this project.
