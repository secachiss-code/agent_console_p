#!/bin/sh
# Install the two locked interpreter profiles without privilege escalation.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd -P)
HOME_DIR=$(CDPATH= cd -- "$HOME" && pwd -P)
allow_outside=0
dry_run=0
for arg in "$@"; do
    case "$arg" in
        --allow-outside-home) allow_outside=1 ;;
        --dry-run) dry_run=1 ;;
        *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
    esac
done
case "$ROOT/" in
    "$HOME_DIR/"*) ;;
    *) if [ "$allow_outside" -ne 1 ]; then
        printf 'Checkout is outside HOME; pass --allow-outside-home to continue.\n' >&2
        exit 1
    fi ;;
esac
printf 'GTK dependencies must be installed by the user; see project documentation.\n'
if [ "$dry_run" -eq 1 ]; then
    printf 'Would sync %s/.venv-core and %s/.venv-ui from uv.lock.\n' "$ROOT" "$ROOT"
    printf 'Would add the AConsole launcher under %s/.local/share.\n' "$HOME_DIR"
    exit 0
fi
UV=${ACONSOLE_UV:-uv}
cd "$ROOT"
UV_PROJECT_ENVIRONMENT="$ROOT/.venv-core" "$UV" sync --locked --no-python-downloads --python 3.15t
UV_PROJECT_ENVIRONMENT="$ROOT/.venv-ui" "$UV" sync --locked --group ui --no-python-downloads --python 3.15+gil
# GTK cannot load under free threading, and the core refuses to run with the GIL. A wrong build must stop here.
"$ROOT/.venv-ui/bin/python" -c 'import sys; sys.exit(not sys._is_gil_enabled())' || {
    printf '.venv-ui is not a GIL build of Python.\n' >&2; exit 1; }
"$ROOT/.venv-core/bin/python" -c 'import sys; sys.exit(sys._is_gil_enabled())' || {
    printf '.venv-core is not a free-threaded build of Python.\n' >&2; exit 1; }
ID=io.github.aconsole.AConsole
if [ -f "$ROOT/packaging/$ID.desktop" ]; then
    SHARE=${XDG_DATA_HOME:-$HOME_DIR/.local/share}
    mkdir -p "$SHARE/applications" "$SHARE/icons/hicolor/scalable/apps" "$SHARE/icons/hicolor/symbolic/apps"
    cp "$ROOT/packaging/$ID.svg" "$SHARE/icons/hicolor/scalable/apps/$ID.svg"
    cp "$ROOT/packaging/$ID-symbolic.svg" "$SHARE/icons/hicolor/symbolic/apps/$ID-symbolic.svg"
    gtk-update-icon-cache -q -t "$SHARE/icons/hicolor" 2>/dev/null || true
    sed "s|^Exec=.*|Exec=$ROOT/.venv-ui/bin/aconsole|" "$ROOT/packaging/$ID.desktop" >"$SHARE/applications/$ID.desktop"
fi
printf 'Installed. Open AConsole from the applications menu, or run %s/.venv-ui/bin/aconsole\n' "$ROOT"
