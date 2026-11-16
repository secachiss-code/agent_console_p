"""Select the UI's renderer before importing GTK (which can initialize GDK)."""

from collections.abc import MutableMapping


def configure_renderer(environment: MutableMapping[str, str]) -> None:
    """Prefer Cairo for this terminal/dashboard UI; keep explicit backend choices.

    GSK_RENDERER alone does not stop GDK probing GL/Vulkan drivers at import.
    Avoid that driver footprint when Cairo is selected. An explicit GDK_DEBUG
    remains authoritative, including for debugging graphics initialization.
    """
    renderer = environment.setdefault("GSK_RENDERER", "cairo")
    if renderer == "cairo":
        _ = environment.setdefault("GDK_DEBUG", "gl-disable:vulkan-disable")
