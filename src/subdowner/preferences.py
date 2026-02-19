"""Preferences window — API keys, provider accounts, download directory."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .config import DOWNLOAD_DIR, save_config


class PreferencesWindow(Adw.PreferencesWindow):
    """Settings dialog for API keys, provider credentials, and paths."""

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

        # --- OpenSubtitles.com account (subliminal provider) ---
        prov = config.get("provider_configs", {})

        os_grp = Adw.PreferencesGroup(
            title="OpenSubtitles.com Account",
            description="Optional — enables scored matching via subliminal.",
        )
        os_cfg = prov.get("opensubtitlescom", {})
        self._os_user = Adw.EntryRow(title="Username")
        self._os_user.set_text(os_cfg.get("username", ""))
        os_grp.add(self._os_user)
        self._os_pass = Adw.PasswordEntryRow(title="Password")
        self._os_pass.set_text(os_cfg.get("password", ""))
        os_grp.add(self._os_pass)
        page.add(os_grp)

        # --- Addic7ed account ---
        a7_grp = Adw.PreferencesGroup(
            title="Addic7ed Account",
            description="Optional — popular for TV series subtitles.",
        )
        a7_cfg = prov.get("addic7ed", {})
        self._a7_user = Adw.EntryRow(title="Username")
        self._a7_user.set_text(a7_cfg.get("username", ""))
        a7_grp.add(self._a7_user)
        self._a7_pass = Adw.PasswordEntryRow(title="Password")
        self._a7_pass.set_text(a7_cfg.get("password", ""))
        a7_grp.add(self._a7_pass)
        page.add(a7_grp)

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

        prov = self._config.setdefault("provider_configs", {})
        for key, user_row, pass_row in [
            ("opensubtitlescom", self._os_user, self._os_pass),
            ("addic7ed", self._a7_user, self._a7_pass),
        ]:
            u, p = user_row.get_text().strip(), pass_row.get_text().strip()
            if u or p:
                prov[key] = {"username": u, "password": p}
            else:
                prov.pop(key, None)

        save_config(self._config)
        self._on_save()
        return False
