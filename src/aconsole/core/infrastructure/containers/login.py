"""How anything starts inside an agent container, on any machine.

Nothing here assumes how a machine set its container up. The agent is looked for in this order:
what the login profile puts on PATH, the npm prefix in `~/.npmrc`, then the usual per-user directories.
"""

from __future__ import annotations

from typing import Final

# Runs after the profile, so a working profile decides. Keep single quotes out: this is one shell word.
_FIND_AND_RUN: Final = r"""
PATH="$PATH:/usr/local/bin:/usr/bin:/bin"
prefix=$(sed -n "s/^prefix=//p" "$HOME/.npmrc" 2>/dev/null | head -n 1)
PATH="$PATH:${prefix:+$prefix/bin:}$HOME/.npm-global/bin:$HOME/.local/bin:$HOME/bin:$HOME/.cargo/bin"
PATH="$PATH:${HOME%/containers/*}/host-bins"
export PATH
if ! command -v "$1" >/dev/null 2>&1; then
    echo "aconsole: $1 is not installed in this container. Looked in: $PATH" >&2
    exit 127
fi
exec "$@" <&3 3<&-
"""

# The profile must not see stdin: distrobox's runs `host-spawn` when `podman exec` brings no XAUTHORITY, and that
# swallows what was meant for the agent. A login shell that cannot run this script (fish) is replaced by sh.
_ENTER: Final = r"""
exec 3<&0 </dev/null
shell=$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7)
case "$shell" in
    */bash|*/zsh|*/ksh|*/dash|*/sh) [ -x "$shell" ] || shell=/bin/sh ;;
    *) shell=/bin/sh ;;
esac
exec "$shell" -lc '""" + _FIND_AND_RUN + r"""' aconsole "$@"
"""

LOGIN_SHELL: Final = ("/bin/sh", "-c", _ENTER, "aconsole")
