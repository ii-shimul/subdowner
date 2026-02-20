"""Data models shared across the application."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubResult:
    """A single subtitle result from any backend."""

    title: str
    language: str
    provider: str
    release: str = ""
    hearing_impaired: bool = False
    download_count: int = 0
    score: int = 0
    matches: list[str] = field(default_factory=list)

    # OpenSubtitles REST API
    os_file_id: int | None = None

    # Direct download URL (e.g. gestdown API)
    download_url: str | None = None
