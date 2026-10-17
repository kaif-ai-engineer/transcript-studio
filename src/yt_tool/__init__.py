"""yt_tool package: YouTube Playlist Transcript Downloader & Study Notes Generator."""

from yt_tool.models import (
    VideoMetadata,
    PlaylistMetadata,
    TranscriptSegment,
    VideoTranscript,
    JobProgress,
)
from yt_tool.playlist import (
    extract_playlist_id,
    fetch_playlist_metadata,
    fetch_playlist_metadata_via_api,
    InvalidPlaylistURLError,
    PlaylistNotFoundError,
    PlaylistExtractionError,
)
from yt_tool.transcript import TranscriptFetcher, format_timestamp
from yt_tool.network import NetworkManager, CircuitBreaker
from yt_tool.cache import TranscriptCache
from yt_tool.processor import JobManager
from yt_tool.formatter import (
    format_combined_markdown,
    format_combined_plaintext,
    format_video_markdown,
    sanitize_filename,
)
from yt_tool.exporter import PlaylistExporter

__all__ = [
    "VideoMetadata",
    "PlaylistMetadata",
    "TranscriptSegment",
    "VideoTranscript",
    "JobProgress",
    "extract_playlist_id",
    "fetch_playlist_metadata",
    "fetch_playlist_metadata_via_api",
    "InvalidPlaylistURLError",
    "PlaylistNotFoundError",
    "PlaylistExtractionError",
    "TranscriptFetcher",
    "format_timestamp",
    "NetworkManager",
    "CircuitBreaker",
    "TranscriptCache",
    "JobManager",
    "format_combined_markdown",
    "format_combined_plaintext",
    "format_video_markdown",
    "sanitize_filename",
    "PlaylistExporter",
]
