"""Search and download backends — OpenSubtitles REST API + subliminal.

All network work happens on caller-supplied threads; nothing here
touches GTK.
"""

from __future__ import annotations

import logging
import os
import threading
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

import chardet

import subliminal
from babelfish import Language
from subliminal import (
    download_subtitles as subliminal_download,
    list_subtitles as subliminal_list,
    region,
)
from subliminal.score import compute_score
from subliminal.video import Video

# Per-provider timeout in seconds. Providers that don't respond in time
# are silently skipped so one dead provider can't block the whole search.
_PROVIDER_TIMEOUT = 10

_subliminal_cache_configured = False


def _ensure_subliminal_cache():
    """Configure subliminal's dogpile cache lazily (on first use)."""
    global _subliminal_cache_configured
    if _subliminal_cache_configured:
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
# Gestdown direct API (show-level subtitle search, no subliminal needed)
# ---------------------------------------------------------------------------

_GESTDOWN_API = "https://api.gestdown.info"


def search_gestdown(
    query: str,
    lang_codes: list[str],
) -> list[SubResult]:
    """Search the Gestdown REST API for TV-show subtitles by name.

    Unlike the subliminal provider (which needs a parsed Episode object),
    this searches for *shows* by name and returns subtitles across all
    seasons—ideal for free-text queries like ``"Breaking Bad"``.

    Season queries are parallelised so the total latency is roughly one
    round-trip instead of ``N × seasons``.
    """
    results: list[SubResult] = []

    # Step 1: Find matching shows.
    try:
        r = requests.get(
            f"{_GESTDOWN_API}/shows/search/{requests.utils.quote(query)}",
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if r.status_code != 200:
            return results
        shows = r.json().get("shows", [])
    except Exception:
        log.debug("gestdown show search failed", exc_info=True)
        return results

    if not shows:
        return results

    # Map alpha-2 → alpha-3 (ISO 639-2/B) for the gestdown API.
    a3_to_a2: dict[str, str] = {}
    lang_a3: list[str] = []
    for code in lang_codes:
        try:
            a3 = Language.fromalpha2(code).alpha3
            lang_a3.append(a3)
            a3_to_a2[a3] = code
        except Exception:
            pass
    if not lang_a3:
        return results

    # Step 2: For the top-matching show, fetch subtitles in parallel.
    show = shows[0]
    show_name = show.get("name", query)
    show_id = show.get("id")
    seasons: list[int] = show.get("seasons", [])

    if not show_id or not seasons:
        return results

    lock = threading.Lock()

    def _fetch_season(season: int, a3: str) -> None:
        """Fetch subtitles for one season/language pair (daemon thread)."""
        try:
            resp = requests.get(
                f"{_GESTDOWN_API}/shows/{show_id}/{season}/{a3}",
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if resp.status_code != 200:
                return
            episodes = resp.json().get("episodes", [])
        except Exception:
            return

        batch: list[SubResult] = []
        for ep in episodes:
            ep_num = ep.get("number", 0)
            ep_season = ep.get("season", season)
            ep_title = ep.get("title", "")

            for sub in ep.get("subtitles", []):
                dl_uri = sub.get("downloadUri", "")
                if not dl_uri:
                    continue
                dl_url = (
                    f"{_GESTDOWN_API}{dl_uri}"
                    if dl_uri.startswith("/")
                    else dl_uri
                )
                title = (
                    f"{show_name} S{ep_season:02d}E{ep_num:02d}"
                    f"{' - ' + ep_title if ep_title else ''}"
                )
                batch.append(
                    SubResult(
                        title=title,
                        language=a3_to_a2.get(a3, "en"),
                        provider="Gestdown",
                        release=sub.get("version", ""),
                        hearing_impaired=sub.get("hearingImpaired", False),
                        score=0,
                        download_url=dl_url,
                    )
                )
        with lock:
            results.extend(batch)

    # Launch all season/language fetches in parallel.
    threads: list[threading.Thread] = []
    for season in seasons:
        for a3 in lang_a3:
            t = threading.Thread(
                target=_fetch_season, args=(season, a3), daemon=True
            )
            t.start()
            threads.append(t)

    for t in threads:
        t.join(timeout=15)

    return results


def download_gestdown(url: str) -> bytes:
    """Download a subtitle file from a Gestdown direct URL."""
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    if not r.content:
        raise RuntimeError("Gestdown returned empty content.")
    return normalize_encoding(r.content)


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

    Each provider is queried in its own thread with a timeout so that
    one unresponsive provider can't block the entire search.

    When *video_path* points to an existing file, ``scan_video()`` is
    used for hash-based matching instead of ``Video.fromname()``.
    """
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

        # Query each provider in its own daemon thread with a timeout.
        all_subs: list = []
        lock = threading.Lock()
        done_event = threading.Event()
        pending = [len(providers)]  # mutable counter

        def _query_provider(provider: str) -> None:
            """Run a single provider search (daemon thread)."""
            try:
                subs = subliminal_list(
                    {video},
                    languages,
                    providers=[provider],
                    provider_configs=configs or None,
                )
                with lock:
                    all_subs.extend(subs.get(video, []))
            except Exception:
                log.debug("provider %s failed", provider, exc_info=True)
            finally:
                with lock:
                    pending[0] -= 1
                    if pending[0] <= 0:
                        done_event.set()

        for p in providers:
            threading.Thread(
                target=_query_provider, args=(p,), daemon=True
            ).start()

        # Wait for all providers or timeout — whichever comes first.
        done_event.wait(timeout=_PROVIDER_TIMEOUT)

        results: list[SubResult] = []
        for sub in all_subs:
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
