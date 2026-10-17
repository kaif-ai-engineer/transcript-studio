"""Local filesystem caching with atomic writes and resume support."""

from __future__ import annotations
import json
import os
import re
import shutil
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from yt_tool.models import PlaylistMetadata, VideoTranscript
from yt_tool.config import DEFAULT_CONFIG

logger = logging.getLogger(__name__)

SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class CacheSecurityError(Exception):
    """Raised when an unsafe identifier is provided to cache operations."""
    pass


def sanitize_id_for_path(identifier: str) -> str:
    """Validate identifier to prevent path traversal."""
    cleaned = (identifier or "").strip()
    if not cleaned or not SAFE_ID_PATTERN.match(cleaned):
        raise CacheSecurityError(f"Invalid or unsafe identifier for cache path: '{cleaned}'")
    return cleaned


def atomic_write_json(file_path: Path, data: Any) -> None:
    """
    Write JSON data to a temporary file and atomically replace target file.
    Prevents file corruption if process is killed or interrupted mid-write.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = file_path.with_suffix(f"{file_path.suffix}.tmp.{os.getpid()}")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, file_path)
    except Exception:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass
        raise


class TranscriptCache:
    """Manages disk-backed storage of playlist metadata and video transcripts."""

    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = Path(root_dir or DEFAULT_CONFIG.cache_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _get_playlist_dir(self, playlist_id: str) -> Path:
        safe_pid = sanitize_id_for_path(playlist_id)
        pdir = (self.root_dir / safe_pid).resolve()
        # Prevent traversal
        if not str(pdir).startswith(str(self.root_dir)):
            raise CacheSecurityError(f"Resolved path outside cache root: {pdir}")
        return pdir

    def _get_transcripts_dir(self, playlist_id: str) -> Path:
        return self._get_playlist_dir(playlist_id) / "transcripts"

    def save_playlist(self, playlist: PlaylistMetadata) -> None:
        """Persist playlist metadata to cache."""
        pdir = self._get_playlist_dir(playlist.playlist_id)
        file_path = pdir / "playlist.json"
        atomic_write_json(file_path, playlist.to_dict())
        logger.debug("Cached playlist metadata to %s", file_path)

    def load_playlist(self, playlist_id: str) -> Optional[PlaylistMetadata]:
        """Load playlist metadata from cache if available."""
        pdir = self._get_playlist_dir(playlist_id)
        file_path = pdir / "playlist.json"
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return PlaylistMetadata.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to load cached playlist %s: %s", playlist_id, exc)
            return None

    def save_transcript(self, playlist_id: str, transcript: VideoTranscript) -> None:
        """Persist a single video transcript to cache atomically."""
        safe_vid = sanitize_id_for_path(transcript.video_id)
        tdir = self._get_transcripts_dir(playlist_id)
        file_path = tdir / f"{safe_vid}.json"
        atomic_write_json(file_path, transcript.to_dict())
        logger.debug("Cached transcript for video %s in playlist %s", transcript.video_id, playlist_id)

    def load_transcript(self, playlist_id: str, video_id: str) -> Optional[VideoTranscript]:
        """Load a single video transcript from cache if present."""
        safe_vid = sanitize_id_for_path(video_id)
        tdir = self._get_transcripts_dir(playlist_id)
        file_path = tdir / f"{safe_vid}.json"
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return VideoTranscript.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to load cached transcript %s/%s: %s", playlist_id, video_id, exc)
            return None

    def has_successful_transcript(self, playlist_id: str, video_id: str) -> bool:
        """Return True if a successfully completed transcript is already cached."""
        t = self.load_transcript(playlist_id, video_id)
        return t is not None and t.is_successful()

    def load_all_transcripts(self, playlist_id: str) -> Dict[str, VideoTranscript]:
        """Load all cached transcripts for a playlist, keyed by video_id."""
        tdir = self._get_transcripts_dir(playlist_id)
        if not tdir.exists():
            return {}
        try:
            results: Dict[str, VideoTranscript] = {}
            for file_path in tdir.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    t = VideoTranscript.from_dict(data)
                    results[t.video_id] = t
                except Exception as exc:
                    logger.warning("Error reading transcript file %s: %s", file_path, exc)
            return results
        except Exception as exc:
            logger.warning("Failed to load all transcripts for %s: %s", playlist_id, exc)
            return {}

    def clear_cache(self, playlist_id: Optional[str] = None) -> None:
        """Clear cache for a specific playlist or all playlists."""
        if playlist_id:
            pdir = self._get_playlist_dir(playlist_id)
            if pdir.exists():
                shutil.rmtree(pdir)
                logger.info("Cleared cache for playlist %s", playlist_id)
        else:
            if self.root_dir.exists():
                for item in self.root_dir.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    elif item.is_file():
                        item.unlink()
                logger.info("Cleared entire cache directory %s", self.root_dir)

    def list_cached_playlists(self) -> List[str]:
        """Return list of playlist IDs currently present in cache."""
        if not self.root_dir.exists():
            return []
        return [
            d.name for d in self.root_dir.iterdir()
            if d.is_dir() and (d / "playlist.json").exists()
        ]
