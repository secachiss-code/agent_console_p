"""Renderer policy must run before GTK and preserve explicit graphics choices."""

from aconsole.ui.rendering import configure_renderer


def test_default_avoids_gpu_driver_probe() -> None:
    environment: dict[str, str] = {}
    configure_renderer(environment)
    assert environment == {"GSK_RENDERER": "cairo", "GDK_DEBUG": "gl-disable:vulkan-disable"}


def test_explicit_accelerated_renderer_is_preserved() -> None:
    environment = {"GSK_RENDERER": "gl"}
    configure_renderer(environment)
    assert environment == {"GSK_RENDERER": "gl"}


def test_explicit_debug_flags_are_preserved() -> None:
    environment = {"GSK_RENDERER": "cairo", "GDK_DEBUG": "frames"}
    configure_renderer(environment)
    assert environment == {"GSK_RENDERER": "cairo", "GDK_DEBUG": "frames"}


def test_repeated_configuration_is_stable() -> None:
    environment: dict[str, str] = {}
    configure_renderer(environment)
    first = environment.copy()
    configure_renderer(environment)
    assert environment == first
