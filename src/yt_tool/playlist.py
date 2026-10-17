"""YouTube playlist URL validation and metadata extraction."""

from __future__ import annotations
import os
import re
import urllib.parse
from typing import Optional, Dict, Any, List
import logging
import requests
import yt_dlp

from yt_tool.models import PlaylistMetadata, VideoMetadata
from yt_tool.config import DEFAULT_CONFIG

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Valid YouTube playlist ID pattern (starts with PL, UU, FL, RD, OLAK, etc., or standard base64url chars)
PLAYLIST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{10,64}$")


class PlaylistError(Exception):
    """Base exception for playlist operations."""
    pass


class InvalidPlaylistURLError(PlaylistError):
    """Raised when an invalid YouTube playlist URL or ID is provided."""
    pass


class PlaylistNotFoundError(PlaylistError):
    """Raised when a playlist cannot be found or is private."""
    pass


class PlaylistExtractionError(PlaylistError):
    """Raised when metadata extraction fails."""
    pass


def extract_playlist_id(url_or_id: str) -> str:
    """
    Extract and validate the YouTube playlist ID from various URL formats or raw IDs.
    
    Supported formats:
    - https://www.youtube.com/playlist?list=PLxxxx
    - https://youtube.com/playlist?list=PLxxxx
    - https://www.youtube.com/watch?v=xxxx&list=PLxxxx
    - https://youtu.be/xxxx?list=PLxxxx
    - Raw ID: PLxxxx, OLAK5uy_xxxx, etc.
    
    Raises:
        InvalidPlaylistURLError: If the input is not a recognized YouTube playlist URL or ID.
    """
    raw = (url_or_id or "").strip()
    if not raw:
        raise InvalidPlaylistURLError("Playlist URL or ID cannot be empty.")

    # Check if raw string is already a valid playlist ID
    if PLAYLIST_ID_PATTERN.match(raw) and not (raw.startswith("http://") or raw.startswith("https://")):
        return raw

    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception as exc:
        raise InvalidPlaylistURLError(f"Malformed URL: {exc}") from exc

    # Security check: Disallow non-HTTP(S) schemes
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        raise InvalidPlaylistURLError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    # Check query string for 'list' parameter
    query_params = urllib.parse.parse_qs(parsed.query)
    if "list" in query_params and query_params["list"]:
        candidate_id = query_params["list"][0].strip()
        if PLAYLIST_ID_PATTERN.match(candidate_id):
            return candidate_id

    # Check path pattern like /playlist/PLxxxx or /playlist?list=
    path_match = re.search(r"/playlist/([a-zA-Z0-9_-]{10,64})", parsed.path)
    if path_match:
        candidate_id = path_match.group(1).strip()
        if PLAYLIST_ID_PATTERN.match(candidate_id):
            return candidate_id

    raise InvalidPlaylistURLError(
        f"Could not identify a valid YouTube playlist ID from input: '{raw}'. "
        "Expected format: https://www.youtube.com/playlist?list=... or a valid playlist ID."
    )


def build_canonical_playlist_url(playlist_id: str) -> str:
    """Build a canonical YouTube playlist URL from a playlist ID."""
    clean_id = extract_playlist_id(playlist_id)
    return f"https://www.youtube.com/playlist?list={clean_id}"


def fetch_playlist_metadata_via_api(
    playlist_id: str,
    api_key: str,
    timeout_seconds: Optional[int] = None,
) -> PlaylistMetadata:
    """Fetch playlist metadata using the official YouTube Data API v3.

    This is the officially-supported, fully Terms-of-Service-compliant path. It
    needs a free API key (Google Cloud Console → enable "YouTube Data API v3").
    Listing a playlist costs only a handful of quota units (50 videos per page).
    """
    timeout = timeout_seconds or DEFAULT_CONFIG.metadata_timeout_seconds

    # 1. Playlist title.
    title = f"YouTube Playlist {playlist_id}"
    try:
        resp = requests.get(
            f"{YOUTUBE_API_BASE}/playlists",
            params={"part": "snippet", "id": playlist_id, "key": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("items") or []
        if items:
            title = items[0].get("snippet", {}).get("title") or title
    except requests.RequestException as exc:
        raise PlaylistExtractionError(f"YouTube Data API request failed: {exc}") from exc

    # 2. Paginate through the playlist items (50 per request).
    videos: List[VideoMetadata] = []
    page_token: Optional[str] = None
    index = 1
    while True:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(f"{YOUTUBE_API_BASE}/playlistItems", params=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise PlaylistExtractionError(f"YouTube Data API request failed: {exc}") from exc

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            video_id = (snippet.get("resourceId") or {}).get("videoId")
            if not video_id:
                continue
            videos.append(
                VideoMetadata(
                    video_id=video_id,
                    title=snippet.get("title") or f"Video {index}",
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    playlist_index=index,
                )
            )
            index += 1

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    logger.info("Found %d video entries via YouTube Data API for '%s'", len(videos), title)
    return PlaylistMetadata(
        playlist_id=playlist_id,
        title=title,
        url=build_canonical_playlist_url(playlist_id),
        total_count=len(videos),
        videos=videos,
    )


def fetch_playlist_metadata(
    url_or_id: str,
    timeout_seconds: Optional[int] = None,
    api_key: Optional[str] = None,
) -> PlaylistMetadata:
    """
    Fetch playlist metadata and its video entries in original playlist order.

    If ``api_key`` (or the ``YOUTUBE_API_KEY`` environment variable) is available,
    the official YouTube Data API v3 is used. Otherwise, yt-dlp flat extraction is
    used as a zero-configuration fallback.

    Uses flat extraction (extract_flat=True) to avoid downloading video streams or
    making individual video requests, completing within seconds even for large playlists.

    Args:
        url_or_id: YouTube playlist URL or ID.
        timeout_seconds: Timeout for network operations in seconds.
        api_key: Optional YouTube Data API v3 key. Falls back to env var.

    Returns:
        PlaylistMetadata with list of VideoMetadata in exact playlist order.
    """
    playlist_id = extract_playlist_id(url_or_id)
    key = api_key or os.environ.get("YOUTUBE_API_KEY", "").strip()

    if key:
        try:
            return fetch_playlist_metadata_via_api(playlist_id, key, timeout_seconds)
        except PlaylistExtractionError as exc:
            logger.warning("Data API metadata failed (%s); falling back to yt-dlp.", exc)

    return _fetch_playlist_metadata_ytdlp(playlist_id, timeout_seconds)


def _fetch_playlist_metadata_ytdlp(
    playlist_id: str,
    timeout_seconds: Optional[int] = None,
) -> PlaylistMetadata:
    """Zero-config metadata extraction using yt-dlp flat mode."""
    canonical_url = build_canonical_playlist_url(playlist_id)
    timeout = timeout_seconds or DEFAULT_CONFIG.metadata_timeout_seconds

    ydl_opts: Dict[str, Any] = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": timeout,
    }

    logger.info("Extracting playlist metadata for playlist ID: %s", playlist_id)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(canonical_url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        err_msg = str(exc)
        if "does not exist" in err_msg or "Private playlist" in err_msg or "Unavailable" in err_msg:
            raise PlaylistNotFoundError(f"Playlist {playlist_id} not found or is private: {err_msg}") from exc
        raise PlaylistExtractionError(f"Failed to extract playlist metadata: {err_msg}") from exc
    except Exception as exc:
        raise PlaylistExtractionError(f"Unexpected error while fetching playlist: {exc}") from exc

    if not info:
        raise PlaylistNotFoundError(f"No playlist data returned for {playlist_id}.")

    title = info.get("title") or f"YouTube Playlist {playlist_id}"
    raw_entries = info.get("entries") or []

    videos: List[VideoMetadata] = []
    # yt-dlp may return a generator or list
    if hasattr(raw_entries, "__iter__"):
        entry_list = list(raw_entries)
    else:
        entry_list = []

    current_index = 1
    for item in entry_list:
        if not item or not isinstance(item, dict):
            continue
        video_id = item.get("id") or item.get("url")
        if not video_id:
            continue
        
        # In flat extraction, id is usually the 11-char video ID
        # If url is returned instead of id, normalize it
        if "/" in video_id or "watch" in video_id:
            vid_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", video_id)
            if vid_match:
                video_id = vid_match.group(1)

        video_title = item.get("title") or f"Video {current_index}"
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        duration = item.get("duration")
        if duration is not None:
            try:
                duration = int(duration)
            except (ValueError, TypeError):
                duration = None

        videos.append(
            VideoMetadata(
                video_id=video_id,
                title=video_title,
                url=video_url,
                playlist_index=current_index,
                duration=duration,
            )
        )
        current_index += 1

    logger.info("Found %d video entries in playlist '%s'", len(videos), title)

    return PlaylistMetadata(
        playlist_id=playlist_id,
        title=title,
        url=canonical_url,
        total_count=len(videos),
        videos=videos,
    )
