"""Run the unchanged desktop failures on the desktop or a private X server."""

import argparse
import os
import secrets
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("--desktop", action="store_true")
parser.add_argument("--label", default="")
args = parser.parse_args()
label = args.label or ("desktop" if args.desktop else "private")
output = root / "artifacts/qa21"
if any((output / (label + suffix)).exists() for suffix in (".log", ".xml", "-geometry.json")):
    raise FileExistsError("Use a fresh --label to preserve existing evidence")
with tempfile.TemporaryDirectory(prefix="qa21-xvfb-") as directory:
    temp = Path(directory)
    if not args.desktop:
        subprocess.run(["/usr/bin/apt-get", "download", "xvfb"], cwd=temp, timeout=90, check=True)
        packages = list(temp.glob("xvfb_*.deb"))
        assert len(packages) == 1
        subprocess.run(["/usr/bin/dpkg-deb", "-x", str(packages[0]), str(temp / "package")], timeout=20, check=True)
    number = next(n for n in range(101, 120) if not Path(f"/tmp/.X11-unix/X{n}").exists())
    display = str(number).encode()
    cookie = secrets.token_bytes(16)
    auth = temp / "Xauthority"
    auth.write_bytes(
        struct.pack(">HH", 65535, 0)
        + struct.pack(">H", len(display))
        + display
        + struct.pack(">H", 18)
        + b"MIT-MAGIC-COOKIE-1"
        + struct.pack(">H", 16)
        + cookie
    )
    auth.chmod(0o600)
    server = (
        None
        if args.desktop
        else subprocess.Popen(
            [
                str(temp / "package/usr/bin/Xvfb"),
                f":{number}",
                "-screen",
                "0",
                "1280x800x24",
                "-nolisten",
                "tcp",
                "-auth",
                str(auth),
            ]
        )
    )
    try:
        for _ in range(0 if server is None else 100):
            if Path(f"/tmp/.X11-unix/X{number}").exists():
                break
            if server.poll() is not None:
                raise RuntimeError("temporary Xvfb failed to start")
            time.sleep(0.05)
        env = dict(os.environ, GDK_BACKEND="x11", GTK_A11Y="test", PYTHONPATH=str(root / "src"))
        if server is not None:
            env.update(DISPLAY=f":{number}", XAUTHORITY=str(auth))
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("BROADWAY_DISPLAY", None)
        with (output / (label + "-geometry.json")).open("w") as stream:
            subprocess.run(
                [sys.executable, str(output / "geometry.py")], env=env, cwd=root, timeout=20, check=True, stdout=stream
            )
        tests = [
            "tests/gui/front/test_b0_window.py",
            "tests/gui/front/test_b1_keys.py",
            "tests/gui/front/test_des3_visual.py",
            "tests/gui/front/test_approval_alerts.py",
            "tests/gui/front/test_preferences_drafts.py",
        ]
        with (output / (label + ".log")).open("w") as stream:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-o",
                    "addopts=",
                    "-q",
                    "--junitxml=" + str(output / (label + ".xml")),
                    *tests,
                ],
                env=env,
                cwd=root,
                timeout=120,
                check=False,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        print(label, result.returncode)
        raise SystemExit(result.returncode)
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=5)
