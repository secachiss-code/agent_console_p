"""AConsole package."""

CORE_VERSION = "0.1.5"
UI_VERSION = "0.0.1"
REST_API_VERSION = "0.0.1"
MCP_VERSION = "0.0.1"

# Release identifier shared by the Python distribution and Debian package.
__version__ = "0.1.5+a"


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
