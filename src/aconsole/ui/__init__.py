"""GTK UI process. It does not import the core package."""

import os

from aconsole.ui.rendering import configure_renderer

# Importing gi.repository.Gtk may initialize a display, before app.run().
# Apply this at the package boundary so every UI entry point gets the same policy.
configure_renderer(os.environ)
