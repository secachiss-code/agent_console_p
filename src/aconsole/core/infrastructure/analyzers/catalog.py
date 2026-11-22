"""Reviewed Open VSX artifacts; updating requires changing these pins in code."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class Artifact:
    namespace: str
    name: str
    version: str
    platform: str
    sha256: str
    dependencies: tuple[str, ...] = ()

    @property
    def extension_id(self) -> str:
        return f"{self.namespace}.{self.name}"


@dataclass(frozen=True, slots=True)
class Analyzer:
    key: str
    artifact: Artifact
    dependencies: tuple[Artifact, ...] = ()


_PYTHON = Artifact(
    "ms-python", "python", "2026.10.0", "linux-x64", "4a05e50fee8d6c613a4a17f3ee1c9c0e318f0d9bcfa636dbe079d9c8e1506e05"
)
CATALOG = MappingProxyType(
    {
        "ruff": Analyzer(
            "ruff",
            Artifact(
                "charliermarsh",
                "ruff",
                "2026.98.0",
                "linux-x64",
                "8526a5f0dd64ba1d7f6bb86e33b89e705fdf7bca3231509a45a7f60cc04cdd6d",
            ),
        ),
        "pylint": Analyzer(
            "pylint",
            Artifact(
                "ms-python",
                "pylint",
                "2026.8.0",
                "universal",
                "5a2cfee3e251434aeee5ff315ce5180dd0b96f85669bc01701ca8c767cc10ca1",
                ("ms-python.python",),
            ),
            (_PYTHON,),
        ),
    }
)


def get_analyzer(key: str) -> Analyzer:
    """Return an approved analyzer; arbitrary registry IDs are rejected."""
    try:
        return CATALOG[key]
    except KeyError:
        message = f"Unknown analyzer: {key}"
        raise ValueError(message) from None
