"""AConsole package."""

__version__ = "0.0.0"


def package_roots() -> tuple[str, ...]:
    """Importable layers created in A0.2. Later tasks fill them in."""
    return (
        "aconsole.contracts",
        "aconsole.core",
        "aconsole.core.application",
        "aconsole.core.domain",
        "aconsole.core.infrastructure",
        "aconsole.devcli",
        "aconsole.sidecar",
        "aconsole.ui",
    )
