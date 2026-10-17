"""Tests for export generation, manifest accuracy, CSV formatting, and ZIP archive contents."""

import io
import json
import zipfile
import csv
from pathlib import Path

from yt_tool.models import PlaylistMetadata, VideoTranscript
from yt_tool.exporter import (
    PlaylistExporter,
    generate_manifest,
    generate_failed_videos_csv,
)
from yt_tool.cache import TranscriptCache


def test_generate_manifest_structure(sample_playlist: PlaylistMetadata, sample_successful_transcripts):
    """Verify manifest dictionary schema, counts, and per-video fields."""
    manifest = generate_manifest(sample_playlist, sample_successful_transcripts)

    assert manifest["playlist_id"] == sample_playlist.playlist_id
    assert manifest["playlist_title"] == sample_playlist.title
    assert manifest["total_videos"] == 3
    assert manifest["successful_count"] == 2
    assert manifest["failed_count"] == 1
    assert len(manifest["videos"]) == 3

    v1 = manifest["videos"][0]
    assert v1["playlist_index"] == 1
    assert v1["video_id"] == "vid_001_first"
    assert v1["status"] == "success"
    assert v1["segment_count"] == 2
    assert v1["character_count"] > 0

    v3 = manifest["videos"][2]
    assert v3["playlist_index"] == 3
    assert v3["status"] == "failed"
    assert v3["error_reason"] == "Captions are disabled for this video."


def test_generate_failed_videos_csv(sample_successful_transcripts):
    """Verify CSV formatting for failed or unavailable videos."""
    csv_str = generate_failed_videos_csv(sample_successful_transcripts)
    reader = csv.reader(io.StringIO(csv_str))
    rows = list(reader)

    # Header row
    assert rows[0] == ["playlist_index", "video_id", "title", "url", "error_reason"]
    # Exactly 1 failed video in sample
    assert len(rows) == 2
    assert rows[1][0] == "3"
    assert rows[1][1] == "vid_003_third"
    assert "Captions are disabled" in rows[1][4]


def test_generate_zip_archive_contents(sample_playlist: PlaylistMetadata, sample_successful_transcripts):
    """Requirement: ZIP containing combined MD, combined TXT, individual_transcripts/, manifest.json, and failed_videos.csv."""
    exporter = PlaylistExporter()
    zip_bytes = exporter.generate_zip_bytes(
        sample_playlist,
        sample_successful_transcripts,
        include_timestamps=True,
    )

    assert len(zip_bytes) > 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        namelist = zf.namelist()

        # Check required files
        assert "combined_transcripts.md" in namelist
        assert "combined_transcripts.txt" in namelist
        assert "manifest.json" in namelist
        assert "failed_videos.csv" in namelist

        # Check individual transcripts directory
        indiv_files = [n for n in namelist if n.startswith("individual_transcripts/")]
        # Only successful videos (2 of them) should be in individual_transcripts
        assert len(indiv_files) == 2

        # Verify manifest inside zip is valid JSON
        manifest_raw = zf.read("manifest.json").decode("utf-8")
        manifest_data = json.loads(manifest_raw)
        assert manifest_data["playlist_id"] == sample_playlist.playlist_id

        # Verify combined MD content inside zip
        md_raw = zf.read("combined_transcripts.md").decode("utf-8")
        assert f"# Study Handbook: {sample_playlist.title}" in md_raw


def test_offline_export_from_cache(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata, sample_successful_transcripts):
    """Requirement: Ensure all exports can be generated from cached transcript data without retrieving transcripts again."""
    # Populate cache
    mock_cache.save_playlist(sample_playlist)
    for t in sample_successful_transcripts:
        mock_cache.save_transcript(sample_playlist.playlist_id, t)

    # Run exporter with cache
    exporter = PlaylistExporter(cache=mock_cache)
    transcripts_from_cache = exporter.build_transcripts_list_from_cache(sample_playlist)

    assert len(transcripts_from_cache) == 3
    assert transcripts_from_cache[0].is_successful()
    assert transcripts_from_cache[1].is_successful()
    assert not transcripts_from_cache[2].is_successful()

    # Generate ZIP completely offline from cache
    zip_bytes = exporter.generate_zip_bytes(sample_playlist, transcripts_from_cache)
    assert len(zip_bytes) > 0
