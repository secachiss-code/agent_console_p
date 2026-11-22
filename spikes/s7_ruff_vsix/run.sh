#!/usr/bin/env bash
set -euo pipefail

SPIKE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${RUFF_VSIX_RUNTIME_DIR:-/tmp/aconsole-ruff-vsix-stage1}"
VSCODIUM_BIN="${VSCODIUM_BIN:-/tmp/aconsole-analyzer-host-check/1.143.17232/bin/codium}"
ELECTRON_LIB_DIR="${ELECTRON_LIB_DIR:-/tmp/aconsole-analyzer-host-check/deps/usr/lib/x86_64-linux-gnu}"
DOWNLOAD_DIR="$RUNTIME_DIR/downloads"
PROFILE_DIR="$RUNTIME_DIR/profile"
EXTENSIONS_DIR="$RUNTIME_DIR/extensions"
WORKSPACE_DIR="$RUNTIME_DIR/workspace"
PROBE_RESULT="$RUNTIME_DIR/diagnostics.json"

for command_name in curl xvfb-run; do
    command -v "$command_name" >/dev/null || {
        printf 'missing required command: %s\n' "$command_name" >&2
        exit 2
    }
done
[[ -x "$VSCODIUM_BIN" ]] || { printf 'VSCodium is not executable: %s\n' "$VSCODIUM_BIN" >&2; exit 2; }
[[ -d "$ELECTRON_LIB_DIR" ]] || { printf 'Electron libraries are missing: %s\n' "$ELECTRON_LIB_DIR" >&2; exit 2; }

mkdir -p "$DOWNLOAD_DIR" "$PROFILE_DIR" "$EXTENSIONS_DIR" "$WORKSPACE_DIR"
rm -f "$PROBE_RESULT" "$RUNTIME_DIR/vscodium.log"

curl --fail --silent --show-error --location \
    https://open-vsx.org/api/charliermarsh/ruff/linux-x64/latest \
    --output "$DOWNLOAD_DIR/ruff-linux-x64-latest.json"
python3 - "$DOWNLOAD_DIR/ruff-linux-x64-latest.json" "$DOWNLOAD_DIR/ruff-metadata.txt" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
if data.get("targetPlatform") != "linux-x64":
    raise SystemExit(f"wrong Open VSX target platform: {data.get('targetPlatform')!r}")
Path(sys.argv[2]).write_text(f"{data['version']}\n{data['targetPlatform']}\n{data['files']['download']}\n")
PY
mapfile -t ruff_metadata < "$DOWNLOAD_DIR/ruff-metadata.txt"
RUFF_VERSION="${ruff_metadata[0]}"
RUFF_PLATFORM="${ruff_metadata[1]}"
RUFF_VSIX_URL="${ruff_metadata[2]}"
RUFF_VSIX="$DOWNLOAD_DIR/charliermarsh.ruff-${RUFF_VERSION}@${RUFF_PLATFORM}.vsix"
curl --fail --silent --show-error --location "$RUFF_VSIX_URL" --output "$RUFF_VSIX"

set +e
timeout 60s xvfb-run -a env \
    LD_LIBRARY_PATH="$ELECTRON_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$VSCODIUM_BIN" \
    --user-data-dir "$PROFILE_DIR" \
    --extensions-dir "$EXTENSIONS_DIR" \
    --install-extension "$RUFF_VSIX" --force \
    >"$RUNTIME_DIR/install.log" 2>&1
install_status=$?
set -e
if (( install_status != 0 )); then
    printf 'FAIL: isolated VSIX installation failed without --no-sandbox (exit %s)\n' "$install_status" >&2
    cat "$RUNTIME_DIR/install.log" >&2
    exit "$install_status"
fi
RUFF_BINARY="$EXTENSIONS_DIR/charliermarsh.ruff-${RUFF_VERSION}/bundled/libs/bin/ruff"
RUFF_BINARY_VERSION="$("$RUFF_BINARY" --version)"

cat > "$WORKSPACE_DIR/pyproject.toml" <<'TOML'
[tool.ruff.lint]
select = ["F"]
TOML
cat > "$WORKSPACE_DIR/.vscode-settings.json" <<'JSON'
{
  "ruff.nativeServer": "on",
  "ruff.logLevel": "debug"
}
JSON
mkdir -p "$WORKSPACE_DIR/.vscode"
cp "$WORKSPACE_DIR/.vscode-settings.json" "$WORKSPACE_DIR/.vscode/settings.json"
cat > "$WORKSPACE_DIR/probe.py" <<'PYTHON'
import os

print("activation proof")
PYTHON

export ACONSOLE_RUFF_PROBE_RESULT="$PROBE_RESULT"
export ACONSOLE_RUFF_PROBE_SOURCE="$WORKSPACE_DIR/probe.py"
set +e
timeout 75s xvfb-run -a env \
    LD_LIBRARY_PATH="$ELECTRON_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$VSCODIUM_BIN" \
    --user-data-dir "$PROFILE_DIR" \
    --extensions-dir "$EXTENSIONS_DIR" \
    --disable-gpu --disable-telemetry --disable-updates \
    --new-window \
    --disable-workspace-trust \
    --extensionDevelopmentPath="$SPIKE_DIR/probe" \
    --wait "$WORKSPACE_DIR/probe.py" \
    >"$RUNTIME_DIR/vscodium.log" 2>&1
codium_status=$?
set -e

if [[ ! -f "$PROBE_RESULT" ]]; then
    printf 'FAIL: probe did not produce diagnostics (VSCodium exit %s)\n' "$codium_status" >&2
    tail -100 "$RUNTIME_DIR/vscodium.log" >&2 || true
    exit 1
fi

python3 - "$PROBE_RESULT" "$RUFF_VERSION" "$RUFF_BINARY_VERSION" "$VSCODIUM_BIN" "$codium_status" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
report["ruff_extension_version"] = sys.argv[2]
report["bundled_ruff_version"] = sys.argv[3]
report["vscodium_binary"] = sys.argv[4]
report["vscodium_exit_code"] = int(sys.argv[5])
report["bridge"] = "native LSP over stdio (JSON-RPC)"
Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + "\n")
codes = {
    str(item.get("code", {}).get("value") if isinstance(item.get("code"), dict) else item.get("code"))
    for item in report.get("diagnostics", [])
}
print(json.dumps(report, indent=2))
if not report.get("probe_activated") or "F401" not in codes:
    raise SystemExit("FAIL: expected probe activation and Ruff F401 diagnostic")
print("PASS: standalone Ruff VSIX activated and published F401")
PY
