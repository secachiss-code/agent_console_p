"""Release notes shipped inside the package."""

from importlib.resources import files

from aconsole.ui.i18n import language


def release_notes(*, lang: str | None = None) -> str:
    """The notes for the running version. Empty when the resource is missing."""
    name = "changelog.txt" if (lang or language()) == "ru" else "changelog.en.txt"
    resource = files("aconsole.ui").joinpath(name)
    if not resource.is_file():
        return ""
    return resource.read_text(encoding="utf-8").strip()
