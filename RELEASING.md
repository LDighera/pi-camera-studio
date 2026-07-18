# Release process

This project publishes GitHub releases, not PyPI releases. A release contains a
Git tag, GitHub's source archives, a Python wheel, a Python source distribution,
and `SHA256SUMS`. Alpha versions must be marked as pre-releases on GitHub.
The wheel contains the Python package, console entry point, bundled model,
model documentation, and license files. The source-only `install.sh` and
`uninstall.sh` scripts are carried by the source distribution, not the wheel.

## Release gate

1. Update `pi_camera_studio.__version__` with a PEP 440 version and add the same
   release to `CHANGELOG.md`. The `0.1.0a1` package version corresponds to the
   human-facing Git tag `v0.1.0-alpha.1`.
2. Run `scripts/release-check` from a clean checkout. This is a non-hardware gate
   and must complete without skipped tests. Its isolated package build installs
   the backend requirements declared in `pyproject.toml`.
3. Complete the supported Raspberry Pi hardware checks in `VERIFICATION.md`.
   Review generated still, video, audio, stop-motion, and detection artifacts
   locally; never add them to a release merely because a command exited zero.
4. Push the candidate commit and require every GitHub Actions CI job to pass.

## Build and publish

Build from the exact candidate commit in a clean checkout:

```sh
python3 -m build
python3 -m twine check --strict dist/*
(cd dist && LC_ALL=C sha256sum *.whl *.tar.gz > SHA256SUMS)
(cd dist && sha256sum -c SHA256SUMS)
```

Inspect the release diff, then create the annotated tag
`v0.1.0-alpha.1`. Create a GitHub pre-release from that exact tag, use the
matching changelog section as its notes, and attach the wheel, source
distribution, and `SHA256SUMS`. Verify the uploaded checksums after downloading
the assets. Do not publish the distributions to PyPI: runtime camera and GUI
dependencies are intentionally managed by Raspberry Pi OS.
