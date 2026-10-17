"""Data models for playlist metadata, transcripts, and job tracking."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Any, Dict
import datetime


@dataclass
class VideoMetadata:
    """Metadata representing a single video within a playlist."""
    video_id: str
    title: str
    url: str
    playlist_index: int  # 1-indexed relative to playlist order
    duration: Optional[int] = None  # Duration in seconds, if available

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VideoMetadata:
        return cls(
            video_id=data["video_id"],
            title=data.get("title", f"Video {data.get('video_id', '')}"),
            url=data.get("url", f"https://www.youtube.com/watch?v={data.get('video_id', '')}"),
            playlist_index=int(data["playlist_index"]),
            duration=data.get("duration"),
        )


@dataclass
class PlaylistMetadata:
    """Metadata representing an entire YouTube playlist."""
    playlist_id: str
    title: str
    url: str
    total_count: int
    videos: List[VideoMetadata] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "playlist_id": self.playlist_id,
            "title": self.title,
            "url": self.url,
            "total_count": self.total_count,
            "videos": [v.to_dict() for v in self.videos],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlaylistMetadata:
        videos = [VideoMetadata.from_dict(v) for v in data.get("videos", [])]
        return cls(
            playlist_id=data["playlist_id"],
            title=data.get("title", "Untitled Playlist"),
            url=data.get("url", ""),
            total_count=int(data.get("total_count", len(videos))),
            videos=videos,
        )


@dataclass
class TranscriptSegment:
    """A timed slice of spoken text from a caption stream."""
    text: str
    start: float  # In seconds
    duration: float  # In seconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TranscriptSegment:
        return cls(
            text=str(data.get("text", "")),
            start=float(data.get("start", 0.0)),
            duration=float(data.get("duration", 0.0)),
        )


@dataclass
class VideoTranscript:
    """The retrieved transcript and status for a single video."""
    video_id: str
    title: str
    url: str
    playlist_index: int
    status: str  # "success", "failed", "skipped"
    language: Optional[str] = None
    language_code: Optional[str] = None
    is_generated: Optional[bool] = None
    segments: List[TranscriptSegment] = field(default_factory=list)
    full_text: str = ""
    error_reason: Optional[str] = None
    retrieved_at: Optional[str] = None

    def __post_init__(self):
        # Enforce requirement: Never mark a transcript successful when its content is missing or incomplete.
        if self.status == "success" and (not self.segments or not self.full_text.strip()):
            self.status = "failed"
            self.error_reason = self.error_reason or "Empty transcript text received"

    def is_successful(self) -> bool:
        return self.status == "success" and len(self.segments) > 0 and bool(self.full_text.strip())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "playlist_index": self.playlist_index,
            "status": self.status,
            "language": self.language,
            "language_code": self.language_code,
            "is_generated": self.is_generated,
            "segments": [s.to_dict() for s in self.segments],
            "full_text": self.full_text,
            "error_reason": self.error_reason,
            "retrieved_at": self.retrieved_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VideoTranscript:
        segments = [TranscriptSegment.from_dict(s) for s in data.get("segments", [])]
        return cls(
            video_id=data["video_id"],
            title=data.get("title", ""),
            url=data.get("url", f"https://www.youtube.com/watch?v={data['video_id']}"),
            playlist_index=int(data.get("playlist_index", 1)),
            status=data.get("status", "failed"),
            language=data.get("language"),
            language_code=data.get("language_code"),
            is_generated=data.get("is_generated"),
            segments=segments,
            full_text=data.get("full_text", ""),
            error_reason=data.get("error_reason"),
            retrieved_at=data.get("retrieved_at"),
        )


@dataclass
class JobProgress:
    """State tracking for a batch transcript retrieval run."""
    total: int = 0
    completed: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    current_video_title: str = ""
    current_video_index: int = 0
    elapsed_seconds: float = 0.0
    is_running: bool = False
    is_cancelled: bool = False
    logs: List[str] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.completed)

    @property
    def progress_percentage(self) -> float:
        if self.total == 0:
            return 0.0
        return min(1.0, max(0.0, self.completed / self.total))
