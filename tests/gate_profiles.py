"""Complete, explicit micro argv for tests which exercise contract admission."""

MICRO_PROFILE = {
    "micro": {
        "format": ["ruff", "format"],
        "fix": ["ruff", "check", "--fix"],
        "lint": ["ruff", "check"],
        "types": ["basedpyright"],
        "tests": ["pytest"],
    },
}
