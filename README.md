# SubDowner

A GTK4 / Libadwaita desktop app for searching and downloading subtitles.

![GTK4](https://img.shields.io/badge/GTK-4-blue)
![Adwaita](https://img.shields.io/badge/Libadwaita-1.x-blue)
![Python](https://img.shields.io/badge/Python-3.10%2B-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## What it does

SubDowner searches for subtitles using two backends simultaneously:

1. **OpenSubtitles REST API** — covers movies and series by plain-text query (requires a free API key from [opensubtitles.com/consumers](https://www.opensubtitles.com/consumers)).
2. **subliminal** — adds results from Gestdown, TVsubtitles, Podnapisi, and optionally OpenSubtitles.com/Addic7ed with account credentials. Includes scoring, video refiners, and parallel provider queries.

Results from both are merged, deduplicated, sorted by score, and shown in a clean Adwaita list.

## Features

- **Hybrid search** across OpenSubtitles REST API + subliminal providers
- **Multi-language** search — pick any combination of 21 languages
- **Scoring & match details** from subliminal's refiners (TMDB, OMDb)
- **"I'm Feeling Lucky"** — one click to download the best match
- **Drag & drop** a video file for hash-based matching
- **Pagination** — load more OpenSubtitles results page by page
- **Search history** with recent queries popover
- **Configurable download directory**, hearing-impaired filter, encoding normalization
- **Keyboard shortcuts** — `Ctrl+F` focus search, `Ctrl+Enter` search, `Ctrl+Q` quit
- **Desktop integration** — `.desktop` file, AppStream metadata, SVG icon

## Installation

### AUR (Arch Linux)

```bash
# Using an AUR helper
yay -S subdowner

# Or manually
git clone https://aur.archlinux.org/subdowner.git
cd subdowner
makepkg -si
```

### System dependencies

SubDowner needs GTK 4 and Libadwaita, which are system packages on most Linux distros:

```bash
# Arch
sudo pacman -S gtk4 libadwaita python-gobject

# Fedora
sudo dnf install gtk4 libadwaita python3-gobject

# Ubuntu / Debian (23.04+)
sudo apt install libgtk-4-dev libadwaita-1-dev python3-gi gir1.2-adw-1
```

### App install

```bash
# Clone the repo
git clone https://github.com/ii-shimul/subdowner.git
cd subdowner

# Create a venv that can see system GTK bindings
python3 -m venv --system-site-packages venv
source venv/bin/activate

# Install in editable mode
pip install -e '.[full]'
```

### Run

```bash
# As a command
subdowner

# Or as a module
python -m subdowner
```

## Configuration

Settings are stored in `~/.config/subdowner/config.json`. You can also edit them from the in-app Preferences window (☰ → Preferences…).

| Key                | Description                                    |
| ------------------ | ---------------------------------------------- |
| `api_key`          | OpenSubtitles REST API key                     |
| `languages`        | List of ISO 639-1 codes (e.g. `["en", "fr"]`)  |
| `download_dir`     | Where subtitle files are saved                 |
| `exclude_hi`       | Hide hearing-impaired subtitles                |
| `provider_configs` | Credentials for OpenSubtitles.com and Addic7ed |
| `search_history`   | Recent search queries (max 20)                 |

## Desktop integration

To install the `.desktop` file and icon system-wide:

```bash
# Icon
sudo install -Dm644 data/io.github.subdowner.svg \
  /usr/share/icons/hicolor/scalable/apps/io.github.subdowner.svg

# Desktop entry (edit Exec= path first!)
sudo desktop-file-install data/io.github.subdowner.desktop

# AppStream metadata
sudo install -Dm644 data/io.github.subdowner.metainfo.xml \
  /usr/share/metainfo/io.github.subdowner.metainfo.xml

# Refresh caches
sudo update-desktop-database
sudo gtk-update-icon-cache /usr/share/icons/hicolor/
```

## Project structure

```
subdowner/
├── src/subdowner/
│   ├── __init__.py        # Package version
│   ├── __main__.py        # python -m subdowner
│   ├── app.py             # GtkApplication, keyboard shortcuts
│   ├── window.py          # Main window — search, results, download
│   ├── preferences.py     # Preferences dialog
│   ├── backend.py         # Search & download logic (no GTK)
│   ├── config.py          # Config load/save, constants
│   └── models.py          # SubResult dataclass
├── data/
│   ├── io.github.subdowner.desktop
│   ├── io.github.subdowner.svg
│   └── io.github.subdowner.metainfo.xml
├── PKGBUILD               # AUR build recipe
├── .SRCINFO               # AUR metadata
├── pyproject.toml
├── LICENSE
└── README.md
```

## Architecture

All network I/O (search, download) runs in **daemon threads**. Results are pushed to the GTK main loop via `GLib.idle_add()`, which guarantees UI updates happen on the main thread. A generation counter (`_search_gen`) prevents stale results from overwriting newer ones.

The backend module has zero GTK dependencies — it only deals with `requests`, `subliminal`, and the `SubResult` dataclass. This makes it testable independently of the UI.

## License

[MIT](LICENSE)
