"""Export generation for Markdown, Plaintext, individual files, manifest, and ZIP archives."""

from __future__ import annotations
import csv
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional

from yt_tool.models import PlaylistMetadata, VideoTranscript
from yt_tool.formatter import (
    format_combined_markdown,
    format_combined_plaintext,
    format_video_markdown,
    sanitize_filename,
)
from yt_tool.cache import TranscriptCache

logger = logging.getLogger(__name__)


def generate_manifest(
    playlist: PlaylistMetadata,
    transcripts: List[VideoTranscript],
) -> Dict[str, Any]:
    """Generate structured manifest dictionary with playlist metadata and per-video stats."""
    ordered = sorted(transcripts, key=lambda t: t.playlist_index)
    successful = [t for t in ordered if t.is_successful()]
    failed = [t for t in ordered if not t.is_successful()]

    video_entries = []
    for t in ordered:
        video_entries.append({
            "playlist_index": t.playlist_index,
            "video_id": t.video_id,
            "title": t.title,
            "url": t.url,
            "status": t.status,
            "language": t.language,
            "language_code": t.language_code,
            "is_generated": t.is_generated,
            "segment_count": len(t.segments),
            "character_count": len(t.full_text),
            "error_reason": t.error_reason,
            "retrieved_at": t.retrieved_at,
        })

    return {
        "playlist_id": playlist.playlist_id,
        "playlist_title": playlist.title,
        "playlist_url": playlist.url,
        "total_videos": playlist.total_count or len(transcripts),
        "successful_count": len(successful),
        "failed_count": len(failed),
        "videos": video_entries,
    }


def generate_failed_videos_csv(transcripts: List[VideoTranscript]) -> str:
    """Generate CSV string containing failed/unavailable videos."""
    ordered = sorted(transcripts, key=lambda t: t.playlist_index)
    failed = [t for t in ordered if not t.is_successful()]

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["playlist_index", "video_id", "title", "url", "error_reason"])
    
    for t in failed:
        writer.writerow([
            t.playlist_index,
            t.video_id,
            t.title,
            t.url,
            t.error_reason or "Unknown error",
        ])
    return output.getvalue()


class PlaylistExporter:
    """Generates study materials and ZIP bundles from transcript data."""

    def __init__(self, cache: Optional[TranscriptCache] = None):
        self.cache = cache or TranscriptCache()

    def build_transcripts_list_from_cache(
        self,
        playlist: PlaylistMetadata,
    ) -> List[VideoTranscript]:
        """
        Build full list of VideoTranscripts using cached data without any network calls.
        For videos without cache, marks them with appropriate status.
        """
        cached_dict = self.cache.load_all_transcripts(playlist.playlist_id)
        transcripts: List[VideoTranscript] = []

        for video in playlist.videos:
            if video.video_id in cached_dict:
                transcripts.append(cached_dict[video.video_id])
            else:
                # Video not cached yet
                transcripts.append(
                    VideoTranscript(
                        video_id=video.video_id,
                        title=video.title,
                        url=video.url,
                        playlist_index=video.playlist_index,
                        status="skipped",
                        error_reason="Transcript not retrieved",
                    )
                )

        return sorted(transcripts, key=lambda t: t.playlist_index)

    def generate_zip_bytes(
        self,
        playlist: PlaylistMetadata,
        transcripts: List[VideoTranscript],
        include_timestamps: bool = True,
    ) -> bytes:
        """
        Create in-memory ZIP archive matching all export requirements:
        - combined_transcripts.md
        - combined_transcripts.txt
        - individual_transcripts/
        - manifest.json
        - failed_videos.csv
        """
        total = playlist.total_count or len(transcripts)
        ordered = sorted(transcripts, key=lambda t: t.playlist_index)

        # 1. Combined Markdown & Plaintext
        combined_md = format_combined_markdown(playlist, ordered, include_timestamps=include_timestamps)
        combined_txt = format_combined_plaintext(playlist, ordered, include_timestamps=include_timestamps)

        # 2. Manifest & CSV
        manifest_data = generate_manifest(playlist, ordered)
        manifest_str = json.dumps(manifest_data, ensure_ascii=False, indent=2)
        failed_csv = generate_failed_videos_csv(ordered)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Add top-level files
            zf.writestr("combined_transcripts.md", combined_md)
            zf.writestr("combined_transcripts.txt", combined_txt)
            zf.writestr("manifest.json", manifest_str)
            zf.writestr("failed_videos.csv", failed_csv)

            # Add individual transcript files
            used_filenames = set()
            for t in ordered:
                if t.is_successful():
                    fname = sanitize_filename(t.title, t.playlist_index, total, extension=".md")
                    # Handle any collision
                    if fname in used_filenames:
                        fname = f"{t.playlist_index:03d}_{t.video_id}.md"
                    used_filenames.add(fname)

                    file_content = format_video_markdown(t, total, include_timestamps=include_timestamps)
                    zf.writestr(f"individual_transcripts/{fname}", file_content)

        return zip_buffer.getvalue()

    def export_to_directory(
        self,
        target_dir: Path,
        playlist: PlaylistMetadata,
        transcripts: List[VideoTranscript],
        include_timestamps: bool = True,
    ) -> Path:
        """Write all export files into a local folder."""
        target_dir.mkdir(parents=True, exist_ok=True)
        total = playlist.total_count or len(transcripts)
        ordered = sorted(transcripts, key=lambda t: t.playlist_index)

        # Write combined MD & TXT
        (target_dir / "combined_transcripts.md").write_text(
            format_combined_markdown(playlist, ordered, include_timestamps=include_timestamps),
            encoding="utf-8",
        )
        (target_dir / "combined_transcripts.txt").write_text(
            format_combined_plaintext(playlist, ordered, include_timestamps=include_timestamps),
            encoding="utf-8",
        )

        # Write manifest & CSV
        (target_dir / "manifest.json").write_text(
            json.dumps(generate_manifest(playlist, ordered), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target_dir / "failed_videos.csv").write_text(
            generate_failed_videos_csv(ordered),
            encoding="utf-8",
        )

        # Write individual files
        indiv_dir = target_dir / "individual_transcripts"
        indiv_dir.mkdir(parents=True, exist_ok=True)
        used_filenames = set()
        for t in ordered:
            if t.is_successful():
                fname = sanitize_filename(t.title, t.playlist_index, total, extension=".md")
                if fname in used_filenames:
                    fname = f"{t.playlist_index:03d}_{t.video_id}.md"
                used_filenames.add(fname)
                (indiv_dir / fname).write_text(
                    format_video_markdown(t, total, include_timestamps=include_timestamps),
                    encoding="utf-8",
                )

        # Write ZIP
        zip_bytes = self.generate_zip_bytes(playlist, ordered, include_timestamps=include_timestamps)
        (target_dir / f"playlist_{playlist.playlist_id}.zip").write_bytes(zip_bytes)

        return target_dir
