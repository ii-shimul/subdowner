"""Preferences window — API key, download directory."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .config import DOWNLOAD_DIR, save_config


class PreferencesWindow(Adw.PreferencesWindow):
    """Settings dialog for API key and download path."""

    def __init__(self, config: dict, on_save, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Preferences")
        self.set_default_size(500, 500)
        self._config = config
        self._on_save = on_save

        page = Adw.PreferencesPage(
            title="Providers", icon_name="network-server-symbolic"
        )

        # --- OpenSubtitles REST API key ---
        api_grp = Adw.PreferencesGroup(
            title="OpenSubtitles REST API",
            description="Get a free key at opensubtitles.com/consumers",
        )
        self._api_key_row = Adw.PasswordEntryRow(title="API Key")
        self._api_key_row.set_text(config.get("api_key", ""))
        api_grp.add(self._api_key_row)
        page.add(api_grp)

        # --- Download directory ---
        dl_grp = Adw.PreferencesGroup(
            title="Download Directory",
            description="Where subtitle files are saved.",
        )
        self._dl_dir_row = Adw.ActionRow(title="Directory")
        self._dl_dir_row.set_subtitle(
            config.get("download_dir", str(DOWNLOAD_DIR))
        )
        choose_btn = Gtk.Button(icon_name="folder-open-symbolic")
        choose_btn.set_valign(Gtk.Align.CENTER)
        choose_btn.connect("clicked", self._pick_folder)
        self._dl_dir_row.add_suffix(choose_btn)
        dl_grp.add(self._dl_dir_row)
        page.add(dl_grp)

        self.add(page)
        self.connect("close-request", self._on_close)

    # -- Folder chooser --

    def _pick_folder(self, _btn):
        dialog = Gtk.FileDialog(title="Choose Download Directory")
        dialog.select_folder(self, None, self._folder_chosen)

    def _folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                self._config["download_dir"] = path
                self._dl_dir_row.set_subtitle(path)
        except GLib.Error:
            pass  # user cancelled

    # -- Persist on close --

    def _on_close(self, _win):
        self._config["api_key"] = self._api_key_row.get_text().strip()

        save_config(self._config)
        self._on_save()
        return False
