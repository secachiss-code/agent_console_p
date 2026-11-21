"""AConsole package."""

CORE_VERSION = "0.1.4"
UI_VERSION = "0.0.1"
REST_API_VERSION = "0.0.1"
MCP_VERSION = "0.0.1"

# Keep package upgrades ordered after 0.1.4+g in both Debian and Python.
__version__ = "0.1.4+ui.0.0.1"


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
