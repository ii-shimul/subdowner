"""Application class and entry point."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from .config import APP_ID
from .window import SubDownerWindow


class SubDownerApp(Adw.Application):
    """Single-instance GTK4/Adwaita application."""

    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("activate", self._on_activate)

        # Register global actions and shortcuts once during init.
        quit_act = Gio.SimpleAction.new("quit", None)
        quit_act.connect("activate", lambda *_: self.quit())
        self.add_action(quit_act)

        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.focus-search", ["<Control>f"])
        self.set_accels_for_action("win.search", ["<Control>Return"])

    def _on_activate(self, _app):
        # Reuse existing window if the app is activated again.
        win = self.get_active_window()
        if not win:
            win = SubDownerWindow(application=self)
        win.present()


def main():
    """CLI entry point."""
    logging.basicConfig(level=logging.WARNING)
    SubDownerApp().run()
