"""Banned-API fixture. Ruff skips this directory. The floor check must reject it."""


class Adw:
    class Spinner:
        """Stand-in for the libadwaita widget that this tree forbids."""


def show() -> Adw.Spinner:
    return Adw.Spinner()
