#!/bin/sh
# SPDX-License-Identifier: MIT
set -eu

app_id=pi-camera-studio
managed_runtime_marker=pi-camera-studio-managed-install=1

fail() {
    printf 'uninstall.sh: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: ./uninstall.sh

Remove the current user's Pi Camera Studio runtime, launcher, and desktop entry.
Captured photographs, videos, stop-motion sequences, and detection snapshots are
not removed.
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

data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
case $data_home in
    /*) ;;
    *) fail "XDG_DATA_HOME must be an absolute path" ;;
esac
[ "$data_home" != / ] || fail "refusing to uninstall with XDG_DATA_HOME=/"

runtime_root=${data_home%/}/$app_id
applications_dir=${data_home%/}/applications
launcher_path=$HOME/.local/bin/$app_id
desktop_path=$applications_dir/$app_id.desktop
removed=false

if [ -L "$runtime_root" ]; then
    fail "refusing to remove runtime symlink: $runtime_root"
fi
if [ -e "$runtime_root" ]; then
    [ -d "$runtime_root" ] || fail "refusing to remove non-directory runtime: $runtime_root"
    runtime_marker=$runtime_root/.pi-camera-studio-install
    if [ ! -f "$runtime_marker" ] || \
        ! grep -qxF "$managed_runtime_marker" "$runtime_marker"
    then
        fail "refusing to remove a runtime without a valid managed-install marker: $runtime_root"
    fi
fi

if [ -L "$launcher_path" ]; then
    fail "refusing to remove launcher symlink: $launcher_path"
fi
if [ -e "$launcher_path" ]; then
    if [ ! -f "$launcher_path" ] || \
        ! grep -qxF '# Pi Camera Studio user-local launcher' "$launcher_path"
    then
        fail "refusing to remove a launcher not managed by Pi Camera Studio: $launcher_path"
    fi
fi

if [ -L "$desktop_path" ]; then
    fail "refusing to remove desktop-entry symlink: $desktop_path"
fi
if [ -e "$desktop_path" ]; then
    if [ ! -f "$desktop_path" ] || \
        ! grep -qxF 'X-Pi-Camera-Studio-Managed=true' "$desktop_path"
    then
        fail "refusing to remove a desktop entry not managed by Pi Camera Studio: $desktop_path"
    fi
fi

# Remove entry points first and the self-contained runtime last. All existing
# paths have been validated above, so an unmanaged file stops the operation
# before anything is removed.
if [ -f "$desktop_path" ]; then
    rm -f -- "$desktop_path"
    removed=true
fi
if [ -f "$launcher_path" ]; then
    rm -f -- "$launcher_path"
    removed=true
fi
if [ -d "$runtime_root" ]; then
    rm -rf -- "$runtime_root"
    removed=true
fi

if command -v update-desktop-database >/dev/null 2>&1 && [ -d "$applications_dir" ]; then
    update-desktop-database "$applications_dir" >/dev/null 2>&1 || \
        printf '%s\n' 'uninstall.sh: warning: desktop menu cache was not refreshed' >&2
fi

if [ "$removed" = true ]; then
    printf '%s\n' 'Pi Camera Studio was removed for the current user.'
else
    printf '%s\n' 'No managed Pi Camera Studio installation was found.'
fi
printf '%s\n' 'Captured media was not removed.'
