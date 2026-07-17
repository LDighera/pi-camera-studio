#!/bin/sh
# SPDX-License-Identifier: MIT
set -eu

umask 022

app_id=pi-camera-studio
managed_runtime_marker=pi-camera-studio-managed-install=1

fail() {
    printf 'install.sh: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: ./install.sh

Install or upgrade Pi Camera Studio for the current desktop user. Do not run
this script with sudo. System packages must be installed separately first.
EOF
}

case ${1:-} in
    "") ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

[ -n "${HOME:-}" ] || fail "HOME is not set"
case $HOME in
    /*) ;;
    *) fail "HOME must be an absolute path" ;;
esac

source_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
package_source=$source_root/pi_camera_studio
desktop_source=$source_root/deploy/pi-camera-studio.desktop
launcher_source=$source_root/deploy/pi-camera-studio

for required_file in \
    "$source_root/LICENSE" \
    "$source_root/NOTICE" \
    "$source_root/README.md" \
    "$source_root/VERIFICATION.md" \
    "$source_root/uninstall.sh" \
    "$package_source/__main__.py" \
    "$package_source/models/model.json" \
    "$desktop_source" \
    "$launcher_source"
do
    [ -f "$required_file" ] || fail "release checkout is incomplete: $required_file is missing"
done

[ -x /usr/bin/python3 ] || fail "/usr/bin/python3 is required"

if ! version=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$source_root" /usr/bin/python3 -c \
    'from pi_camera_studio import __version__; print(__version__)')
then
    fail "could not read the application version from pi_camera_studio.__version__"
fi
[ -n "$version" ] || fail "pi_camera_studio.__version__ is empty"

if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$source_root" /usr/bin/python3 - \
    "$package_source/models/model.json" <<'PY'
import sys
from pathlib import Path

from pi_camera_studio.config import inspect_model

inspection = inspect_model(Path(sys.argv[1]))
if inspection.integrity_ok:
    raise SystemExit(0)

details = []
if inspection.manifest_error:
    details.append(inspection.manifest_error)
elif inspection.manifest is not None:
    manifest = inspection.manifest
    if not inspection.model_integrity_ok:
        details.append(
            "model mismatch "
            f"(expected {manifest.size} bytes / {manifest.sha256}, "
            f"found {inspection.model_size} bytes / {inspection.model_sha256})"
        )
    if not inspection.license_integrity_ok:
        details.append(
            "model-license mismatch "
            f"(expected {manifest.license_size} bytes / {manifest.license_sha256}, "
            f"found {inspection.license_size} bytes / {inspection.license_sha256})"
        )

message = "; ".join(details) or "unknown integrity failure"
raise SystemExit(f"install.sh: bundled model assets failed integrity validation: {message}")
PY
then
    exit 1
fi

if ! PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -c \
    'import av, cv2, libcamera, numpy, picamera2; import PIL; import PyQt5' \
    >/dev/null 2>&1
then
    cat >&2 <<'EOF'
install.sh: required Python modules are missing.

Install the Raspberry Pi/Debian packages documented in README.md, then run
./install.sh again. A pip-only virtual environment is not supported because
Picamera2 and libcamera are supplied by the operating system.
EOF
    exit 1
fi

for required_command in ffmpeg ffprobe; do
    command -v "$required_command" >/dev/null 2>&1 || \
        fail "$required_command is required; install the packages documented in README.md"
done

data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
case $data_home in
    /*) ;;
    *) fail "XDG_DATA_HOME must be an absolute path" ;;
esac
[ "$data_home" != / ] || fail "refusing to install with XDG_DATA_HOME=/"

runtime_root=${data_home%/}/$app_id
applications_dir=${data_home%/}/applications
bin_dir=$HOME/.local/bin
launcher_path=$bin_dir/$app_id
desktop_path=$applications_dir/$app_id.desktop
runtime_marker=$runtime_root/.pi-camera-studio-install

if [ -L "$runtime_root" ]; then
    fail "refusing to replace symlink: $runtime_root"
fi
if [ -e "$runtime_root" ]; then
    [ -d "$runtime_root" ] || fail "refusing to replace non-directory runtime: $runtime_root"
    if [ ! -f "$runtime_marker" ] || \
        ! grep -qxF "$managed_runtime_marker" "$runtime_marker"
    then
        fail "refusing to replace an installation without a valid managed-install marker: $runtime_root"
    fi
fi
if [ -L "$launcher_path" ]; then
    fail "refusing to replace launcher symlink: $launcher_path"
fi
if [ -e "$launcher_path" ]; then
    if [ ! -f "$launcher_path" ] || \
        ! grep -qxF '# Pi Camera Studio user-local launcher' "$launcher_path"
    then
        fail "refusing to replace an existing launcher not managed by Pi Camera Studio: $launcher_path"
    fi
fi
if [ -L "$desktop_path" ]; then
    fail "refusing to replace desktop-entry symlink: $desktop_path"
fi
if [ -e "$desktop_path" ]; then
    if [ ! -f "$desktop_path" ] || \
        ! grep -qxF 'X-Pi-Camera-Studio-Managed=true' "$desktop_path"
    then
        fail "refusing to replace an existing desktop entry not managed by Pi Camera Studio: $desktop_path"
    fi
fi

install -d -m 0755 "$data_home" "$applications_dir" "$bin_dir"

stage=$(mktemp -d "${data_home%/}/.${app_id}.install.XXXXXX")
desktop_tmp=
launcher_tmp=
runtime_backup=
launcher_backup=
desktop_backup=
runtime_activated=false
launcher_activated=false
desktop_activated=false
committed=false

cleanup() {
    cleanup_status=$?
    trap - 0 HUP INT TERM
    if [ -n "${desktop_tmp:-}" ] && [ -f "$desktop_tmp" ]; then
        rm -f -- "$desktop_tmp"
    fi
    if [ -n "${launcher_tmp:-}" ] && [ -f "$launcher_tmp" ]; then
        rm -f -- "$launcher_tmp"
    fi
    if [ -n "${stage:-}" ] && [ -d "$stage" ]; then
        rm -rf -- "$stage"
    fi

    if [ "$committed" != true ]; then
        if [ "$desktop_activated" = true ]; then
            rm -f -- "$desktop_path" || true
        fi
        if [ -n "${desktop_backup:-}" ] && [ -f "$desktop_backup" ]; then
            mv -- "$desktop_backup" "$desktop_path" || true
        fi
        if [ "$launcher_activated" = true ]; then
            rm -f -- "$launcher_path" || true
        fi
        if [ -n "${launcher_backup:-}" ] && [ -f "$launcher_backup" ]; then
            mv -- "$launcher_backup" "$launcher_path" || true
        fi
        if [ "$runtime_activated" = true ] && [ -d "$runtime_root" ]; then
            rm -rf -- "$runtime_root" || true
        fi
        if [ -n "${runtime_backup:-}" ] && [ -d "$runtime_backup" ]; then
            mv -- "$runtime_backup" "$runtime_root" || true
        fi
    fi
    exit "$cleanup_status"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

cp -a -- "$package_source" "$stage/pi_camera_studio"
find "$stage/pi_camera_studio" -type f -name '*.pyc' -exec rm -f -- {} +
find "$stage/pi_camera_studio" -depth -type d -name __pycache__ -exec rm -rf -- {} +
install -m 0644 "$source_root/LICENSE" "$stage/LICENSE"
install -m 0644 "$source_root/NOTICE" "$stage/NOTICE"
install -m 0644 "$source_root/README.md" "$stage/README.md"
install -m 0644 "$source_root/VERIFICATION.md" "$stage/VERIFICATION.md"
install -m 0755 "$source_root/uninstall.sh" "$stage/uninstall.sh"
printf '%s\nversion=%s\n' "$managed_runtime_marker" "$version" > \
    "$stage/.pi-camera-studio-install"

launcher_tmp=$(mktemp "${bin_dir%/}/.${app_id}.XXXXXX")
install -m 0755 "$launcher_source" "$launcher_tmp"

# These single-quoted sed expressions intentionally contain literal shell
# metacharacters required by the Desktop Entry quoting rules.
# shellcheck disable=SC2016
desktop_escaped=$(printf '%s' "$launcher_path" | sed \
    -e 's/\\/\\\\/g' \
    -e 's/"/\\"/g' \
    -e 's/`/\\`/g' \
    -e 's/\$/\\$/g' \
    -e 's/%/%%/g')
desktop_tmp=$(mktemp "${applications_dir%/}/.${app_id}.XXXXXX.desktop")
while IFS= read -r line || [ -n "$line" ]; do
    case $line in
        "Exec=$app_id") printf 'Exec="%s"\n' "$desktop_escaped" ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$desktop_source" > "$desktop_tmp"
chmod 0644 "$desktop_tmp"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$desktop_tmp" || fail "generated desktop entry is invalid"
fi

runtime_backup=${runtime_root}.previous.$$
launcher_backup=${launcher_path}.previous.$$
desktop_backup=${desktop_path}.previous.$$
for backup_path in "$runtime_backup" "$launcher_backup" "$desktop_backup"; do
    if [ -e "$backup_path" ] || [ -L "$backup_path" ]; then
        fail "temporary backup path already exists: $backup_path"
    fi
done

if [ -d "$runtime_root" ]; then
    mv -- "$runtime_root" "$runtime_backup"
else
    runtime_backup=
fi
if [ -f "$launcher_path" ]; then
    mv -- "$launcher_path" "$launcher_backup"
else
    launcher_backup=
fi
if [ -f "$desktop_path" ]; then
    mv -- "$desktop_path" "$desktop_backup"
else
    desktop_backup=
fi

mv -- "$stage" "$runtime_root"
stage=
runtime_activated=true

mv -- "$launcher_tmp" "$launcher_path"
launcher_tmp=
launcher_activated=true

mv -- "$desktop_tmp" "$desktop_path"
desktop_tmp=
desktop_activated=true
committed=true

if [ -n "$runtime_backup" ] && [ -d "$runtime_backup" ]; then
    rm -rf -- "$runtime_backup" || \
        printf 'install.sh: warning: old runtime backup remains at %s\n' "$runtime_backup" >&2
fi
if [ -n "$launcher_backup" ] && [ -f "$launcher_backup" ]; then
    rm -f -- "$launcher_backup" || \
        printf 'install.sh: warning: old launcher backup remains at %s\n' "$launcher_backup" >&2
fi
if [ -n "$desktop_backup" ] && [ -f "$desktop_backup" ]; then
    rm -f -- "$desktop_backup" || \
        printf 'install.sh: warning: old desktop-entry backup remains at %s\n' "$desktop_backup" >&2
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$applications_dir" >/dev/null 2>&1 || \
        printf '%s\n' 'install.sh: warning: desktop menu cache was not refreshed' >&2
fi

printf 'Installed Pi Camera Studio %s for the current user.\n' "$version"
printf '  Runtime: %s\n' "$runtime_root"
printf '  Launcher: %s\n' "$launcher_path"
printf '  Desktop entry: %s\n' "$desktop_path"
case :$PATH: in
    *:"$bin_dir":*) ;;
    *) printf 'Add %s to PATH to run pi-camera-studio by name in a terminal.\n' "$bin_dir" ;;
esac
