"""Configuration management — load, save, and sensible defaults."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from . import __version__

APP_ID = "io.github.subdowner"
APP_NAME = "SubDowner"
VERSION = __version__
API_BASE = "https://api.opensubtitles.com/api/v1"

CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "subdowner"
)
CONFIG_FILE = CONFIG_DIR / "config.json"
DOWNLOAD_DIR = Path.home() / "Downloads"

# Supported languages as (code, label) pairs.
LANGUAGES = [
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("pl", "Polish"),
    ("ru", "Russian"),
    ("ar", "Arabic"),
    ("zh", "Chinese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("tr", "Turkish"),
    ("sv", "Swedish"),
    ("da", "Danish"),
    ("fi", "Finnish"),
    ("el", "Greek"),
    ("cs", "Czech"),
    ("ro", "Romanian"),
    ("hu", "Hungarian"),
]
LANG_CODES = [code for code, _ in LANGUAGES]
LANG_LABELS = [label for _, label in LANGUAGES]
LANG_MAP: dict[str, str] = dict(LANGUAGES)   # code → human label

# Recognised video file extensions (for drag-and-drop).
VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".vob", ".ogv",
}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

_DEFAULTS: dict = {
    "languages": ["en"],
    "api_key": "",
    "download_dir": str(DOWNLOAD_DIR),
    "exclude_hi": False,
    "search_history": [],
}


def load_config() -> dict:
    """Read config from disk, falling back to defaults for missing keys."""
    cfg = dict(_DEFAULTS)
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as fh:
                stored = json.load(fh)
                cfg.update(stored)
                # Migrate legacy single-language key.
                if "language" in stored and "languages" not in stored:
                    cfg["languages"] = [stored["language"]]
    except (json.JSONDecodeError, OSError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    """Persist config to disk (file permissions 0600)."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as fh:
            json.dump(cfg, fh, indent=2)
        os.chmod(CONFIG_FILE, 0o600)
    except OSError as exc:
        log.error("Failed to save config: %s", exc)
