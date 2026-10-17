"""Transcript text and markdown formatting, filename sanitization, and study handbook generation."""

from __future__ import annotations
import re
import datetime
from typing import List, Optional

from yt_tool.models import PlaylistMetadata, VideoTranscript, TranscriptSegment
from yt_tool.transcript import format_timestamp


def sanitize_filename(
    title: str,
    playlist_index: int,
    total_videos: int = 100,
    extension: str = ".md",
) -> str:
    """
    Generate a safe, cross-platform filename preserving playlist order with a numeric prefix.
    
    Examples:
        sanitize_filename("Intro: What is Python?", 1, 137) -> "001 - Intro - What is Python.md"
    """
    digits = max(2, len(str(total_videos)))
    prefix = f"{playlist_index:0{digits}d} - "
    
    # Strip illegal filename characters on Windows, macOS, Linux
    clean = re.sub(r'[\/\\:\*\?"<>\|\x00-\x1f]', " - ", title or "Untitled")
    # Collapse multiple dashes or spaces
    clean = re.sub(r"\s+", " ", clean)
    clean = re.sub(r"-(\s*-)+", "-", clean).strip(" .-_")
    
    # Limit base title length to 80 chars to avoid OS path length limits
    if len(clean) > 80:
        clean = clean[:80].rstrip(" .-_")
        
    if not clean:
        clean = f"Video_{playlist_index}"

    ext = extension if extension.startswith(".") else f".{extension}"
    return f"{prefix}{clean}{ext}"


def format_transcript_body(transcript: VideoTranscript, include_timestamps: bool = True) -> str:
    """
    Format transcript segments into readable text with or without timestamps.
    """
    if not transcript.segments:
        return transcript.full_text or "*[No transcript text available]*"

    if not include_timestamps:
        return transcript.full_text or "\n\n".join(seg.text for seg in transcript.segments if seg.text.strip())

    lines: List[str] = []
    current_para: List[str] = []
    last_end = 0.0

    for seg in transcript.segments:
        text = seg.text.strip()
        if not text:
            continue
        ts = format_timestamp(seg.start)
        
        # Break paragraph if pause > 4.0 seconds
        if current_para and (seg.start - last_end) > 4.0:
            lines.append(" ".join(current_para))
            current_para = []

        current_para.append(f"{ts} {text}")
        last_end = seg.start + seg.duration

    if current_para:
        lines.append(" ".join(current_para))

    return "\n\n".join(lines)


def format_video_markdown(
    transcript: VideoTranscript,
    total_videos: int,
    include_timestamps: bool = True,
) -> str:
    """Format an individual video transcript section in Markdown."""
    digits = max(2, len(str(total_videos)))
    index_str = f"{transcript.playlist_index:0{digits}d}"
    lang = transcript.language or "Unknown"

    body = format_transcript_body(transcript, include_timestamps=include_timestamps)

    lines = [
        f'<a id="video-{index_str}"></a>',
        f"## {index_str}. {transcript.title}",
        "",
        f"- **YouTube URL:** [{transcript.url}]({transcript.url})",
        f"- **Video ID:** `{transcript.video_id}`",
        f"- **Playlist Position:** {transcript.playlist_index} of {total_videos}",
        f"- **Language:** {lang}",
        "",
        "### Transcript",
        "",
        body,
        "",
        "---",
    ]
    return "\n".join(lines)


def format_combined_markdown(
    playlist: PlaylistMetadata,
    transcripts: List[VideoTranscript],
    include_timestamps: bool = True,
) -> str:
    """
    Create one handbook-ready combined Markdown file for the entire playlist.
    
    Includes:
    - Playlist title and source URL
    - Generation date
    - Table of contents with anchor links
    - Individual sections in playlist order
    - Incomplete/failed video summary section
    """
    total = playlist.total_count or len(transcripts)
    digits = max(2, len(str(total)))
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Sort transcripts strictly by playlist_index
    ordered = sorted(transcripts, key=lambda t: t.playlist_index)
    successful = [t for t in ordered if t.is_successful()]
    failed = [t for t in ordered if not t.is_successful()]

    out: List[str] = [
        f"# Study Handbook: {playlist.title}",
        "",
        f"- **Source Playlist URL:** [{playlist.url}]({playlist.url})",
        f"- **Generated On:** {now_str}",
        f"- **Total Videos in Playlist:** {total}",
        f"- **Successfully Transcribed:** {len(successful)}",
        f"- **Failed / Unavailable:** {len(failed)}",
        f"- **Timestamps Included:** {'Yes' if include_timestamps else 'No'}",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
    ]

    for t in successful:
        idx_str = f"{t.playlist_index:0{digits}d}"
        clean_title = t.title.replace("[", "(").replace("]", ")")
        out.append(f"{t.playlist_index}. [{idx_str} - {clean_title}](#video-{idx_str})")

    out.append("")
    out.append("---")
    out.append("")

    for t in successful:
        out.append(format_video_markdown(t, total_videos=total, include_timestamps=include_timestamps))
        out.append("")

    if failed:
        out.extend([
            "## Incomplete / Unavailable Videos",
            "",
            "The transcripts for the following videos could not be retrieved:",
            "",
            "| # | Video ID | Title | URL | Reason |",
            "|---|----------|-------|-----|--------|",
        ])
        for t in failed:
            idx_str = f"{t.playlist_index:0{digits}d}"
            clean_title = t.title.replace("|", "/")
            reason = (t.error_reason or "Unknown error").replace("|", "/")
            out.append(f"| {idx_str} | `{t.video_id}` | {clean_title} | [{t.url}]({t.url}) | {reason} |")
        out.append("")

    return "\n".join(out)


def format_combined_plaintext(
    playlist: PlaylistMetadata,
    transcripts: List[VideoTranscript],
    include_timestamps: bool = True,
) -> str:
    """Create one clean plain-text file containing all transcripts in playlist order."""
    total = playlist.total_count or len(transcripts)
    digits = max(2, len(str(total)))
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    ordered = sorted(transcripts, key=lambda t: t.playlist_index)
    successful = [t for t in ordered if t.is_successful()]
    failed = [t for t in ordered if not t.is_successful()]

    divider = "=" * 80
    subdivider = "-" * 80

    out: List[str] = [
        divider,
        f"STUDY NOTES: {playlist.title}",
        f"Source URL: {playlist.url}",
        f"Generated: {now_str}",
        f"Total Videos: {total} | Successful: {len(successful)} | Failed: {len(failed)}",
        divider,
        "",
    ]

    for t in successful:
        idx_str = f"{t.playlist_index:0{digits}d}"
        lang = t.language or "Unknown"
        body = format_transcript_body(t, include_timestamps=include_timestamps)

        out.extend([
            subdivider,
            f"VIDEO {idx_str}: {t.title}",
            f"URL: {t.url} | ID: {t.video_id} | Language: {lang}",
            subdivider,
            "",
            body,
            "",
            "",
        ])

    if failed:
        out.extend([
            divider,
            "UNAVAILABLE / FAILED VIDEOS",
            divider,
            "",
        ])
        for t in failed:
            idx_str = f"{t.playlist_index:0{digits}d}"
            reason = t.error_reason or "Unknown error"
            out.append(f"[{idx_str}] {t.title} ({t.url}) - Reason: {reason}")
        out.append("")

    return "\n".join(out)
