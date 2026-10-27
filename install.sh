#!/bin/sh
# AConsole install.sh v0 — interpreters and virtualenvs.
# Does not use sudo and does not recreate containers.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
cd "$ROOT"
UV=/home/sas/.containers/containers/devtools/.cargo/bin/uv

cat <<'EOF'
Host packages for the GTK UI build. Run this yourself; this script does not:

  sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 gir1.2-gtksource-5 libgirepository-2.0-dev libcairo2-dev pkg-config build-essential

Checked 2026-10-24: libcairo2-dev and build-essential are installed.
Missing: gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 gir1.2-gtksource-5 libgirepository-2.0-dev pkg-config.
uv 0.13.5 is already on the host.

Interpreter profiles:
  .venv-core  uv venv --python 3.15t   free-threaded, for aconsoled
  .venv-ui    uv venv --python 3.15    GIL, for scripts and the UI
EOF

"$UV" python install 3.15t 3.15
"$UV" venv --no-project --no-python-downloads --python 3.15t --allow-existing "$ROOT/.venv-core"
"$UV" venv --no-project --no-python-downloads --python 3.15 --allow-existing "$ROOT/.venv-ui"
"$UV" pip install --python "$ROOT/.venv-core/bin/python" pytest
"$UV" pip install --python "$ROOT/.venv-ui/bin/python" pytest
