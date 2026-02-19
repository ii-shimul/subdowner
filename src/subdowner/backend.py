"""Search and download backends — OpenSubtitles REST API + subliminal.

All network work happens on caller-supplied threads; nothing here
touches GTK.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import requests

from .config import (
    API_BASE,
    APP_NAME,
    CONFIG_DIR,
    DOWNLOAD_DIR,
    FREE_PROVIDERS,
)
from .models import SubResult

log = logging.getLogger(__name__)

# Optional: chardet for encoding normalisation.
try:
    import chardet
except ImportError:
    chardet = None  # type: ignore[assignment]

# Optional: subliminal for the secondary search backend.
try:
    import subliminal
    from babelfish import Language
    from subliminal import (
        download_subtitles as subliminal_download,
        list_subtitles as subliminal_list,
        region,
    )
    from subliminal.score import compute_score
    from subliminal.video import Video

    HAS_SUBLIMINAL = True
except ImportError:
    HAS_SUBLIMINAL = False

try:
    from subliminal.core import AsyncProviderPool
except ImportError:
    AsyncProviderPool = None

_subliminal_cache_configured = False


def _ensure_subliminal_cache():
    """Configure subliminal's dogpile cache lazily (on first use)."""
    global _subliminal_cache_configured
    if _subliminal_cache_configured or not HAS_SUBLIMINAL:
        return
    _CACHE_FILE = CONFIG_DIR / "subliminal_cache.dbm"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    region.configure(
        "dogpile.cache.dbm",
        arguments={"filename": str(_CACHE_FILE)},
        replace_existing_backend=True,
    )
    _subliminal_cache_configured = True


# ---------------------------------------------------------------------------
# Encoding helper
# ---------------------------------------------------------------------------

def normalize_encoding(data: bytes) -> bytes:
    """Detect charset and re-encode to UTF-8 when possible."""
    if chardet is None:
        return data
    try:
        detected = chardet.detect(data)
        encoding = (detected.get("encoding") or "utf-8").lower()
        if encoding in ("utf-8", "ascii"):
            return data
        return data.decode(encoding, errors="replace").encode("utf-8")
    except Exception:
        return data


# ---------------------------------------------------------------------------
# OpenSubtitles REST API
# ---------------------------------------------------------------------------

def search_opensubtitles(
    query: str,
    lang_codes: list[str],
    api_key: str,
    page: int = 1,
) -> tuple[list[SubResult], int]:
    """Full-text search via the OpenSubtitles v1 REST API.

    Returns ``(results, total_pages)`` so the caller can paginate.
    """
    resp = requests.get(
        f"{API_BASE}/subtitles",
        headers={"Api-Key": api_key, "User-Agent": APP_NAME},
        params={
            "query": query,
            "languages": ",".join(lang_codes),
            "page": page,
        },
        timeout=20,
    )
    resp.raise_for_status()
    body = resp.json()
    total_pages = body.get("total_pages", 1) or 1

    results: list[SubResult] = []
    for item in body.get("data", []):
        attr = item.get("attributes", {})
        files = attr.get("files", [])
        if not files:
            continue
        f = files[0]
        dl_count = attr.get("download_count", 0) or 0
        results.append(
            SubResult(
                title=f.get("file_name", "unknown.srt"),
                language=attr.get("language", lang_codes[0] if lang_codes else "en"),
                provider="OpenSubtitles",
                release=attr.get("release", ""),
                hearing_impaired=attr.get("hearing_impaired", False),
                download_count=dl_count,
                score=dl_count,
                os_file_id=f.get("file_id"),
            )
        )
    return results, total_pages


# ---------------------------------------------------------------------------
# subliminal (multi-provider, scored)
# ---------------------------------------------------------------------------

def search_subliminal(
    query: str,
    lang_codes: list[str],
    provider_configs: dict | None = None,
    video_path: str | None = None,
) -> list[SubResult]:
    """Search subliminal providers with scoring and optional refiners.

    When *video_path* points to an existing file, ``scan_video()`` is
    used for hash-based matching instead of ``Video.fromname()``.
    """
    if not HAS_SUBLIMINAL:
        return []

    _ensure_subliminal_cache()

    try:
        if video_path and os.path.isfile(video_path):
            video = subliminal.scan_video(video_path)
        else:
            video = Video.fromname(query)

        languages = {Language.fromalpha2(c) for c in lang_codes}

        # Build the provider list.
        providers = list(FREE_PROVIDERS)
        configs: dict = {}
        if provider_configs:
            for key in ("opensubtitlescom", "addic7ed"):
                cfg = provider_configs.get(key, {})
                if cfg.get("username") and cfg.get("password"):
                    providers.append(key)
                    configs[key] = cfg

        pool_kwargs: dict = {}
        if AsyncProviderPool is not None:
            pool_kwargs["pool_class"] = AsyncProviderPool

        # Try with refiners first; fall back for older subliminal.
        try:
            subs_dict = subliminal_list(
                {video},
                languages,
                providers=providers,
                provider_configs=configs or None,
                refiners=["tmdb", "omdb"],
                **pool_kwargs,
            )
        except TypeError:
            subs_dict = subliminal_list(
                {video},
                languages,
                providers=providers,
                provider_configs=configs or None,
            )

        results: list[SubResult] = []
        for sub in subs_dict.get(video, []):
            try:
                sub_matches = sorted(sub.get_matches(video))
                sub_score = compute_score(sub, video)
            except Exception:
                sub_matches, sub_score = [], 0

            info = getattr(sub, "info", "") or str(sub.subtitle_id)
            release = (
                getattr(sub, "release_info", None)
                or getattr(sub, "movie_release_name", None)
                or ""
            )
            results.append(
                SubResult(
                    title=info,
                    language=str(sub.language),
                    provider=sub.provider_name,
                    release=release,
                    hearing_impaired=getattr(sub, "hearing_impaired", False) or False,
                    score=sub_score,
                    matches=sub_matches,
                    subliminal_sub=sub,
                )
            )
        return results
    except Exception:
        log.warning("subliminal search failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def download_opensubtitles(file_id: int, api_key: str) -> bytes:
    """Two-step download via the OpenSubtitles REST API.

    1. POST /download  → get a temporary link.
    2. GET that link   → the actual subtitle bytes.
    """
    resp = requests.post(
        f"{API_BASE}/download",
        headers={
            "Api-Key": api_key,
            "User-Agent": APP_NAME,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"file_id": file_id},
        timeout=20,
    )
    resp.raise_for_status()
    link = resp.json().get("link")
    if not link:
        raise RuntimeError("API returned no download link.")

    sub_resp = requests.get(link, timeout=30)
    sub_resp.raise_for_status()
    return normalize_encoding(sub_resp.content)


def download_subliminal_sub(
    sub: Any,
    provider_configs: dict | None = None,
) -> bytes:
    """Download a subliminal ``Subtitle`` object and return its content."""
    providers = list(FREE_PROVIDERS)
    if provider_configs:
        for key in ("opensubtitlescom", "addic7ed"):
            if provider_configs.get(key):
                providers.append(key)

    subliminal_download(
        [sub],
        providers=providers,
        provider_configs=provider_configs or None,
    )
    content = getattr(sub, "content", None)
    if not content:
        raise RuntimeError("Provider returned empty content.")
    return normalize_encoding(content)
