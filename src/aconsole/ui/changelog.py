"""Release notes shipped inside the package."""

from importlib.resources import files


def release_notes() -> str:
    """The notes for the running version. Empty when the resource is missing."""
    resource = files("aconsole.ui").joinpath("changelog.txt")
    if not resource.is_file():
        return ""
    return resource.read_text(encoding="utf-8").strip()
