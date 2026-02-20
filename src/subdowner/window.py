"""Main application window — search, results list, download."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import gi
import requests

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gio, Gtk  # noqa: E402

log = logging.getLogger(__name__)

from .backend import (
    download_gestdown,
    download_opensubtitles,
    parse_video_filename,
    search_gestdown,
    search_opensubtitles,
)
from .config import (
    APP_NAME,
    DOWNLOAD_DIR,
    LANG_CODES,
    LANG_MAP,
    LANGUAGES,
    VERSION,
    VIDEO_EXTENSIONS,
    load_config,
    save_config,
)
from .models import SubResult
from .preferences import PreferencesWindow


class SubDownerWindow(Adw.ApplicationWindow):
    """The one-and-only window of the app."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(780, 580)
        self.set_title(APP_NAME)

        self.config: dict = load_config()
        self.results: list[SubResult] = []
        self.selected_result: SubResult | None = None
        self.lang_checks: dict[str, Gtk.CheckButton] = {}

        # Search state
        self._search_gen = 0          # bumped each search to cancel stale ones
        self._last_query = ""
        self._last_langs: list[str] = []
        self._os_page = 1
        self._os_total_pages = 1
        self._video_path: str | None = None
        self._video_info: dict | None = None  # guessit-parsed metadata
        self._visible_count = 0       # how many result rows are shown so far
        self._PAGE_SIZE = 20
        self._last_download: str | None = None

        self._build_ui()
        self._register_actions()

        if not self.config.get("api_key"):
            self._toast(
                "Tip: set your OpenSubtitles API key in Preferences (☰ menu).",
                timeout=8,
            )

    # ------------------------------------------------------------------
    # Actions (keyboard-shortcut targets live here)
    # ------------------------------------------------------------------

    def _register_actions(self):
        for name, cb in [
            ("preferences", self._show_preferences),
            ("about", self._show_about),
            ("focus-search", lambda *_: self.search_entry.grab_focus()),
            ("search", lambda *_: self._on_search(None)),
        ]:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title=APP_NAME, subtitle="Subtitle Downloader")
        )
        menu = Gio.Menu()
        menu.append("Preferences…", "win.preferences")
        menu.append("About", "win.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_btn)
        root.append(header)

        # Toast overlay wraps all content
        self.toast_overlay = Adw.ToastOverlay()

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_top(12)
        body.set_margin_bottom(12)

        # --- Search bar + history button ---
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(
            "e.g.  Breaking Bad S01E01,  Inception,  The Matrix 1999 …"
        )
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._on_search)
        search_row.append(self.search_entry)

        self.history_btn = Gtk.MenuButton(
            icon_name="document-open-recent-symbolic",
            tooltip_text="Recent searches",
        )
        self.history_btn.add_css_class("flat")
        self._rebuild_history_popover()
        search_row.append(self.history_btn)
        body.append(search_row)

        # --- Options toolbar ---
        opts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        opts.set_halign(Gtk.Align.START)

        opts.append(Gtk.Label(label="Languages"))
        self.lang_button = Gtk.MenuButton()
        self.lang_button.add_css_class("flat")
        self._build_lang_popover()
        self._refresh_lang_label()
        opts.append(self.lang_button)

        self.search_button = Gtk.Button(label="Search")
        self.search_button.add_css_class("suggested-action")
        self.search_button.connect("clicked", self._on_search)
        opts.append(self.search_button)

        self.lucky_button = Gtk.Button(
            label="I'm Feeling Lucky",
            tooltip_text="Search & auto-download the best match",
        )
        self.lucky_button.connect("clicked", self._on_lucky)
        opts.append(self.lucky_button)

        # HI filter
        hi_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hi_box.set_valign(Gtk.Align.CENTER)
        lbl = Gtk.Label(label="Exclude HI", tooltip_text="Hide hearing-impaired subs")
        hi_box.append(lbl)
        self.hi_switch = Gtk.Switch(active=self.config.get("exclude_hi", False))
        self.hi_switch.set_valign(Gtk.Align.CENTER)
        self.hi_switch.connect("notify::active", self._on_hi_toggled)
        hi_box.append(self.hi_switch)
        opts.append(hi_box)

        self.spinner = Gtk.Spinner()
        opts.append(self.spinner)
        body.append(opts)

        # --- Results list ---
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self._on_row_selected)
        self.listbox.connect("row-activated", self._on_row_activated)

        placeholder = Gtk.Label(label="Search results will appear here")
        placeholder.add_css_class("dim-label")
        placeholder.set_margin_top(32)
        placeholder.set_margin_bottom(32)
        self.listbox.set_placeholder(placeholder)

        self.scroll.set_child(self.listbox)
        body.append(self.scroll)

        # Status
        self.status_label = Gtk.Label(label="", xalign=0)
        self.status_label.add_css_class("dim-label")
        body.append(self.status_label)

        # Download button (standalone, no Load More here — it lives in the list)
        self.download_btn = Gtk.Button(label="Download Selected Subtitle")
        self.download_btn.add_css_class("suggested-action")
        self.download_btn.add_css_class("pill")
        self.download_btn.set_sensitive(False)
        self.download_btn.set_hexpand(True)
        self.download_btn.connect("clicked", self._on_download)
        body.append(self.download_btn)

        # "Load More" row — lives at the bottom of the listbox, not in the button area
        self._load_more_row = Gtk.ListBoxRow(selectable=False, activatable=False)
        load_more_btn = Gtk.Button(label="Show More Results")
        load_more_btn.add_css_class("flat")
        load_more_btn.set_hexpand(True)
        load_more_btn.set_margin_top(4)
        load_more_btn.set_margin_bottom(4)
        load_more_btn.connect("clicked", self._on_load_more)
        self._load_more_row.set_child(load_more_btn)

        self.toast_overlay.set_child(body)
        root.append(self.toast_overlay)
        self.set_content(root)

        # Drag-and-drop for video files
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_file_drop)
        root.add_controller(drop)

    # ------------------------------------------------------------------
    # Language selector
    # ------------------------------------------------------------------

    def _build_lang_popover(self):
        popover = Gtk.Popover()
        scroll = Gtk.ScrolledWindow()
        scroll.set_max_content_height(350)
        scroll.set_propagate_natural_height(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        selected = set(self.config.get("languages", ["en"]))
        for code, label in LANGUAGES:
            cb = Gtk.CheckButton(label=label, active=code in selected)
            cb.connect("toggled", self._on_lang_toggled, code)
            box.append(cb)
            self.lang_checks[code] = cb

        scroll.set_child(box)
        popover.set_child(scroll)
        self.lang_button.set_popover(popover)

    def _on_lang_toggled(self, _cb, code):
        langs = self._selected_langs()
        if not langs:
            self.lang_checks[code].set_active(True)
            return
        self.config["languages"] = langs
        self._refresh_lang_label()

    def _selected_langs(self) -> list[str]:
        return [c for c in LANG_CODES
                if self.lang_checks.get(c) and self.lang_checks[c].get_active()]

    def _refresh_lang_label(self):
        sel = self._selected_langs()
        if len(sel) <= 2:
            self.lang_button.set_label(", ".join(LANG_MAP.get(c, c) for c in sel))
        else:
            self.lang_button.set_label(f"{len(sel)} languages")

    # ------------------------------------------------------------------
    # Search history
    # ------------------------------------------------------------------

    def _rebuild_history_popover(self):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        history: list[str] = self.config.get("search_history", [])
        if not history:
            lbl = Gtk.Label(label="No recent searches")
            lbl.add_css_class("dim-label")
            box.append(lbl)
        else:
            for entry in history:
                btn = Gtk.Button(label=entry)
                btn.add_css_class("flat")
                btn.set_halign(Gtk.Align.START)
                btn.connect("clicked", self._pick_history, entry, popover)
                box.append(btn)

            box.append(Gtk.Separator())
            clear = Gtk.Button(label="Clear History")
            clear.add_css_class("flat")
            clear.add_css_class("error")
            clear.connect("clicked", self._clear_history, popover)
            box.append(clear)

        popover.set_child(box)
        self.history_btn.set_popover(popover)

    def _pick_history(self, _btn, query: str, popover: Gtk.Popover):
        popover.popdown()
        self.search_entry.set_text(query)
        self._on_search(None)

    def _clear_history(self, _btn, popover: Gtk.Popover):
        popover.popdown()
        self.config["search_history"] = []
        save_config(self.config)
        self._rebuild_history_popover()
        self._toast("Search history cleared.")

    def _push_history(self, query: str):
        history: list[str] = self.config.get("search_history", [])
        if query in history:
            history.remove(query)
        history.insert(0, query)
        self.config["search_history"] = history[:20]
        save_config(self.config)
        self._rebuild_history_popover()

    # ------------------------------------------------------------------
    # Drag-and-drop
    # ------------------------------------------------------------------

    def _on_file_drop(self, _target, value, _x, _y) -> bool:
        if not isinstance(value, Gio.File):
            return False
        path = value.get_path()
        if not path:
            return False
        if Path(path).suffix.lower() not in VIDEO_EXTENSIONS:
            self._toast("That doesn't look like a video file.")
            return False

        self._video_path = path
        info = parse_video_filename(Path(path).name)
        self._video_info = info

        # Build a human-friendly query from the parsed metadata.
        title = info.get("title", Path(path).stem)
        parts = [title]
        if "season" in info:
            parts.append(f"S{info['season']:02d}")
            if "episode" in info:
                # Replace last part with combined SxxExx.
                parts[-1] += f"E{info['episode']:02d}"
        elif "year" in info:
            parts.append(str(info["year"]))

        display = " ".join(parts)
        self.search_entry.set_text(display)
        self._toast(f"Parsed: {display}")
        self._on_search(None)
        return True

    # ------------------------------------------------------------------
    # HI toggle
    # ------------------------------------------------------------------

    def _on_hi_toggled(self, switch, _pspec):
        self.config["exclude_hi"] = switch.get_active()
        save_config(self.config)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search(self, _widget):
        query = self.search_entry.get_text().strip()
        if not query:
            self._toast("Enter a movie or series name to search.")
            return

        langs = self._selected_langs()
        self.config["languages"] = langs
        save_config(self.config)
        self._push_history(query)

        # If user typed manually (not from drag-and-drop), try parsing
        # the query itself in case it looks like a filename.
        if self._video_info is None:
            info = parse_video_filename(query)
            if info.get("title"):
                self._video_info = info

        self._set_busy(True)
        self._clear_results()

        self._search_gen += 1
        gen = self._search_gen
        self._last_query = query
        self._last_langs = langs
        self._os_page = 1
        self._os_total_pages = 1

        vi = self._video_info
        self._video_info = None   # consumed — reset for the next search

        threading.Thread(
            target=self._search_worker, args=(query, langs, gen, vi), daemon=True
        ).start()

    def _search_worker(
        self, query: str, langs: list[str], gen: int, vi: dict | None
    ):
        """Run both backends in the current (non-UI) thread."""
        results: list[SubResult] = []
        errors: list[str] = []

        # Extract season/episode from guessit metadata when available.
        vi = vi or {}
        search_title = vi.get("title", query)
        os_season = vi.get("season")
        os_episode = vi.get("episode")

        # Primary — OpenSubtitles REST API
        api_key = self.config.get("api_key", "")
        if api_key:
            try:
                os_results, total_pages = search_opensubtitles(
                    search_title, langs, api_key,
                    season_number=os_season,
                    episode_number=os_episode,
                )
                self._os_total_pages = total_pages
                results.extend(os_results)
            except requests.HTTPError as exc:
                code = exc.response.status_code
                errors.append(
                    "OpenSubtitles: invalid API key" if code == 401
                    else f"OpenSubtitles: HTTP {code}"
                )
            except Exception as exc:
                errors.append(f"OpenSubtitles: {exc}")

        # Secondary — gestdown direct API (works for show-name queries)
        try:
            results.extend(
                search_gestdown(
                    search_title, langs,
                    season=os_season,
                    episode=os_episode,
                )
            )
        except Exception:
            log.warning("gestdown direct search failed", exc_info=True)

        # Sort, deduplicate, filter
        results.sort(key=lambda r: r.score, reverse=True)

        seen: set[str] = set()
        deduped: list[SubResult] = []
        for r in results:
            key = r.title.lower().strip()
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        results = deduped

        if self.config.get("exclude_hi", False):
            results = [r for r in results if not r.hearing_impaired]

        # Drop stale results if a newer search already started.
        if gen != self._search_gen:
            return

        if errors and not results:
            GLib.idle_add(self._search_failed, "; ".join(errors))
        else:
            GLib.idle_add(self._populate_results, results, errors)

    # ------------------------------------------------------------------
    # Populate results list
    # ------------------------------------------------------------------

    def _populate_results(self, results: list[SubResult], warnings: list[str]) -> bool:
        self._set_busy(False)
        self.results = results
        self._visible_count = 0

        if warnings:
            self._toast("; ".join(warnings))
        if not results:
            self._toast("No subtitles found.")
            self.status_label.set_text("0 results")
            return False

        self._show_next_batch()
        self._toast(f"Found {len(results)} subtitle(s).")
        return False

    def _show_next_batch(self):
        """Append up to PAGE_SIZE rows from self.results and toggle the load-more row."""
        # Remember scroll position so appending rows doesn't jump to top
        vadj = self.scroll.get_vadjustment()
        saved_pos = vadj.get_value()

        # Remove the load-more row if it's currently in the list
        if self._load_more_row.get_parent() is not None:
            self.listbox.remove(self._load_more_row)

        start = self._visible_count
        end = min(start + self._PAGE_SIZE, len(self.results))
        for i in range(start, end):
            self.listbox.append(self._make_result_row(self.results[i]))
        self._visible_count = end

        # Restore scroll position after GTK finishes laying out the new rows
        if start > 0:
            GLib.idle_add(vadj.set_value, saved_pos)

        # Update status
        total = len(self.results)
        self.status_label.set_text(
            f"Showing {self._visible_count} of {total} result{'s' if total != 1 else ''}"
        )

        # Show "load more" if there are unseen local results OR more API pages
        has_local = self._visible_count < len(self.results)
        has_remote = self._os_page < self._os_total_pages
        if has_local or has_remote:
            self.listbox.append(self._load_more_row)

    def _make_result_row(self, r: SubResult) -> Adw.ActionRow:
        """Build a single ActionRow for a subtitle result."""
        row = Adw.ActionRow()
        row.set_title(GLib.markup_escape_text(r.title))

        parts: list[str] = [r.provider, LANG_MAP.get(r.language, r.language)]
        if r.download_count:
            parts.append(f"DL: {r.download_count:,}")
        if r.hearing_impaired:
            parts.append("♿ HI")
        if r.release:
            parts.append(r.release)
        if r.matches:
            parts.append("✓ " + ", ".join(r.matches))
        row.set_subtitle(GLib.markup_escape_text(" · ".join(parts)))

        row.add_prefix(Gtk.Image.new_from_icon_name("document-text-symbolic"))
        if r.score:
            badge = Gtk.Label(label=str(r.score))
            badge.add_css_class("dim-label")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
        return row

    def _search_failed(self, message: str) -> bool:
        self._set_busy(False)
        self._toast(f"Search failed: {message}")
        return False

    # ------------------------------------------------------------------
    # Row selection / activation
    # ------------------------------------------------------------------

    def _on_row_selected(self, _lb, row):
        if row and 0 <= (i := row.get_index()) < self._visible_count:
            self.selected_result = self.results[i]
            self.download_btn.set_sensitive(True)
            return
        self.selected_result = None
        self.download_btn.set_sensitive(False)

    def _on_row_activated(self, _lb, row):
        """Double-click or Enter → instant download."""
        if row and 0 <= (i := row.get_index()) < self._visible_count:
            self.selected_result = self.results[i]
            self._on_download(None)

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def _on_load_more(self, _btn):
        # If there are unseen local results, just show the next batch
        if self._visible_count < len(self.results):
            self._show_next_batch()
            return
        # Otherwise fetch the next API page
        if self._os_page >= self._os_total_pages:
            return
        self._os_page += 1
        self.spinner.start()
        threading.Thread(target=self._load_more_worker, daemon=True).start()

    def _load_more_worker(self):
        api_key = self.config.get("api_key", "")
        if not api_key:
            GLib.idle_add(self._toast, "No API key configured.")
            return
        try:
            new, _ = search_opensubtitles(
                self._last_query, self._last_langs, api_key, page=self._os_page
            )
            if self.config.get("exclude_hi", False):
                new = [r for r in new if not r.hearing_impaired]
            GLib.idle_add(self._append_results, new)
        except Exception as exc:
            GLib.idle_add(self._toast, f"Load more failed: {exc}")
            GLib.idle_add(self.spinner.stop)

    def _append_results(self, new: list[SubResult]) -> bool:
        self.spinner.stop()
        self.results.extend(new)
        self._show_next_batch()
        return False

    # ------------------------------------------------------------------
    # "I'm Feeling Lucky"
    # ------------------------------------------------------------------

    def _on_lucky(self, _btn):
        query = self.search_entry.get_text().strip()
        if not query:
            self._toast("Enter a movie or series name first.")
            return

        langs = self._selected_langs()
        self.config["languages"] = langs
        save_config(self.config)

        # Parse query for structured metadata.
        if self._video_info is None:
            info = parse_video_filename(query)
            if info.get("title"):
                self._video_info = info

        vi = self._video_info
        self._video_info = None   # consumed

        self._set_busy(True)
        self._clear_results()
        threading.Thread(
            target=self._lucky_worker, args=(query, langs, vi), daemon=True
        ).start()

    def _lucky_worker(self, query: str, langs: list[str], vi: dict | None):
        """Pick the highest-scored result across both backends, download it."""
        try:
            all_results: list[SubResult] = []
            vi = vi or {}
            search_title = vi.get("title", query)
            szn = vi.get("season")
            ep = vi.get("episode")

            api_key = self.config.get("api_key", "")
            if api_key:
                try:
                    os_res, _ = search_opensubtitles(
                        search_title, langs, api_key,
                        season_number=szn,
                        episode_number=ep,
                    )
                    all_results.extend(os_res)
                except Exception:
                    pass

            try:
                all_results.extend(
                    search_gestdown(search_title, langs, season=szn, episode=ep)
                )
            except Exception:
                pass

            if not all_results:
                GLib.idle_add(self._search_failed, "No subtitles found.")
                return

            all_results.sort(key=lambda r: r.score, reverse=True)
            best = all_results[0]
            GLib.idle_add(
                self._toast,
                f"Best match: {best.title[:60]} (score {best.score}). Downloading…",
            )
            self._do_download(best)
        except Exception as exc:
            GLib.idle_add(self._download_done, "", str(exc))

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _on_download(self, _btn):
        if not self.selected_result:
            return
        self._set_busy(True)
        threading.Thread(
            target=self._do_download, args=(self.selected_result,), daemon=True
        ).start()

    def _do_download(self, result: SubResult):
        """Download a subtitle via the appropriate backend (background thread)."""
        try:
            dl_dir = Path(self.config.get("download_dir", str(DOWNLOAD_DIR)))
            dl_dir.mkdir(parents=True, exist_ok=True)

            if result.os_file_id is not None:
                data = download_opensubtitles(
                    result.os_file_id, self.config.get("api_key", "")
                )
                dest = dl_dir / result.title
                dest.write_bytes(data)

            elif result.download_url is not None:
                data = download_gestdown(result.download_url)
                safe = "".join(
                    c if c.isalnum() or c in " .-_()" else "_"
                    for c in result.title
                ) or "subtitle"
                dest = dl_dir / f"{safe}.srt"
                dest.write_bytes(data)

            else:
                raise RuntimeError("No download method available.")

            GLib.idle_add(self._download_done, str(dest), None)

        except requests.HTTPError as exc:
            msg = f"HTTP {exc.response.status_code}"
            try:
                msg += f": {exc.response.json().get('message', '')}"
            except Exception:
                pass
            GLib.idle_add(self._download_done, "", msg)
        except Exception as exc:
            GLib.idle_add(self._download_done, "", str(exc))

    def _download_done(self, path: str, error: str | None) -> bool:
        self._set_busy(False)
        if error:
            self._toast(f"Download failed: {error}")
        else:
            self._last_download = path
            toast = Adw.Toast(title=f"Saved → {Path(path).name}")
            toast.set_timeout(8)
            toast.set_button_label("Open Folder")
            toast.connect("button-clicked", self._open_folder)
            self.toast_overlay.add_toast(toast)
        return False

    def _open_folder(self, _toast):
        path = self._last_download
        if path:
            try:
                Gio.AppInfo.launch_default_for_uri(
                    Path(path).parent.as_uri(), None
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    def _show_preferences(self, _action, _param):
        prefs = PreferencesWindow(
            config=self.config, on_save=self._prefs_saved, transient_for=self
        )
        prefs.present()

    def _prefs_saved(self):
        self._toast("Settings saved.")

    def _show_about(self, _action, _param):
        Adw.AboutWindow(
            transient_for=self,
            application_name=APP_NAME,
            application_icon="io.github.subdowner",
            version=VERSION,
            developer_name="SubDowner Contributors",
            website="https://github.com/ii-shimul/subdowner",
            comments=(
                "Search and download subtitles for movies and TV series.\n\n"
                "Backends: OpenSubtitles REST API + Gestdown.\n"
                "Features: multi-language, drag-and-drop, "
                "keyboard shortcuts, encoding normalization."
            ),
            license_type=Gtk.License.MIT_X11,
        ).present()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool):
        self.spinner.start() if busy else self.spinner.stop()
        self.search_button.set_sensitive(not busy)
        self.lucky_button.set_sensitive(not busy)
        self.search_entry.set_sensitive(not busy)
        self.download_btn.set_sensitive(not busy and self.selected_result is not None)

    def _clear_results(self):
        self.results.clear()
        self._visible_count = 0
        self.selected_result = None
        self.download_btn.set_sensitive(False)
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        self.status_label.set_text("")

    def _toast(self, msg: str, timeout: int = 5):
        t = Adw.Toast(title=msg)
        t.set_timeout(timeout)
        self.toast_overlay.add_toast(t)
