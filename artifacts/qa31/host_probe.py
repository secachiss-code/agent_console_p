"""Bounded environment discovery; no image pulls, containers or package installs."""
# ruff: noqa: INP001 -- standalone evidence script, not an importable package

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def probe(argv: list[str]) -> dict[str, object]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)  # noqa: S603 -- fixed diagnostic argv below
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"argv": argv, "error": str(error)}
    else:
        return {"argv": argv, "exit_code": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}


def main() -> None:
    result = {"python": sys.executable, "uid": os.getuid(),
              "os_release": Path("/etc/os-release").read_text(),
              "tools": {name: shutil.which(name) for name in
                        ("podman", "unshare", "bwrap", "proot", "chroot", "dpkg-deb", "apt-get")}}
    result["namespace_capability"] = probe(["unshare", "--user", "--map-root-user", "--mount", "true"])
    if result["tools"]["podman"]:
        info = probe(["podman", "info", "--format", "json"])
        if info.get("exit_code") == 0:
            data = json.loads(info.pop("stdout"))
            info["selected_fields"] = {
                "rootless": data.get("host", {}).get("security", {}).get("rootless"),
                "graph_driver": data.get("store", {}).get("graphDriverName"),
                "version": data.get("version", {}).get("Version"),
            }
        result["podman_info"] = info
        images = probe(["podman", "images", "--format", "json"])
        if images.get("exit_code") == 0:
            data = json.loads(images.pop("stdout"))
            images["image_count"] = len(data)
            images["ubuntu_24_04_candidates"] = [
                {"id": item.get("Id"), "names": item.get("Names")}
                for item in data if any("ubuntu:24.04" in name for name in item.get("Names", []) or [])
            ]
        result["podman_images"] = images
    result["limit"] = "Availability only: no new container, install, mount or package-manager transaction."
    destination = Path(__file__).with_name("host-environment.json")
    with destination.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))  # noqa: T201 -- report CLI result


if __name__ == "__main__":
    main()
