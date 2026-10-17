"""Job processing, bounded concurrent retrieval, retry management, and cancellation support."""

from __future__ import annotations
import concurrent.futures
import logging
import random
import threading
import time
from typing import List, Optional, Callable, Set, Dict

from yt_tool.models import PlaylistMetadata, VideoMetadata, VideoTranscript, JobProgress
from yt_tool.transcript import TranscriptFetcher
from yt_tool.cache import TranscriptCache
from yt_tool.config import DEFAULT_CONFIG
from yt_tool.network import NetworkManager

logger = logging.getLogger(__name__)


class JobManager:
    """Manages batch transcript retrieval jobs with caching, bounded concurrency, and cancellation."""

    def __init__(
        self,
        fetcher: Optional[TranscriptFetcher] = None,
        cache: Optional[TranscriptCache] = None,
        network_manager: Optional[NetworkManager] = None,
    ):
        self.network_manager = network_manager or NetworkManager()
        self.fetcher = fetcher or TranscriptFetcher(network_manager=self.network_manager)
        self.cache = cache or TranscriptCache()

    def run_job(
        self,
        playlist: PlaylistMetadata,
        selected_video_ids: Optional[Set[str]] = None,
        preferred_languages: Optional[List[str]] = None,
        concurrency: int = DEFAULT_CONFIG.default_concurrency,
        request_delay: float = DEFAULT_CONFIG.request_delay_seconds,
        stop_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[JobProgress, Optional[VideoTranscript]], None]] = None,
        only_failed: bool = False,
    ) -> List[VideoTranscript]:
        """
        Execute transcript processing for the playlist.
        
        Args:
            playlist: The playlist metadata containing video entries.
            selected_video_ids: Specific subset of video IDs to process (default: all).
            preferred_languages: Priority list of language codes.
            concurrency: Maximum number of concurrent worker threads (1-5).
            request_delay: Delay between requests in seconds to avoid rate limiting.
            stop_event: Threading event to signal graceful cancellation.
            progress_callback: Callback invoked whenever progress or a video state changes.
            only_failed: If True, only re-processes videos that are missing or failed.
        """
        start_time = time.time()
        self.cache.save_playlist(playlist)
        cached_transcripts = self.cache.load_all_transcripts(playlist.playlist_id)

        # Filter eligible videos
        selected_set = selected_video_ids if selected_video_ids is not None else {v.video_id for v in playlist.videos}
        target_videos: List[VideoMetadata] = [
            v for v in playlist.videos if v.video_id in selected_set
        ]

        # Determine which videos need network fetching
        videos_to_fetch: List[VideoMetadata] = []
        already_handled: Dict[str, VideoTranscript] = {}

        for v in target_videos:
            cached = cached_transcripts.get(v.video_id)
            if cached and cached.is_successful() and not only_failed:
                already_handled[v.video_id] = cached
            elif cached and not cached.is_successful() and not only_failed:
                # Video previously failed and not explicitly asked to retry
                already_handled[v.video_id] = cached
            else:
                videos_to_fetch.append(v)

        # Initialize progress
        progress = JobProgress(
            total=len(target_videos),
            completed=len(already_handled),
            successful=sum(1 for t in already_handled.values() if t.is_successful()),
            failed=sum(1 for t in already_handled.values() if not t.is_successful() and t.status == "failed"),
            skipped=sum(1 for t in already_handled.values() if t.status == "skipped"),
            elapsed_seconds=0.0,
            is_running=True,
            is_cancelled=False,
            logs=[],
        )

        if already_handled:
            progress.logs.append(f"Loaded {len(already_handled)} videos from local cache.")

        if progress_callback:
            progress_callback(progress, None)

        # If all target videos are already cached or stopped
        if not videos_to_fetch or (stop_event and stop_event.is_set()):
            progress.is_running = False
            progress.elapsed_seconds = time.time() - start_time
            if stop_event and stop_event.is_set():
                progress.is_cancelled = True
                progress.logs.append("Processing stopped by user.")
            if progress_callback:
                progress_callback(progress, None)
            return self._build_result_list(playlist, already_handled)

        # Bounded concurrent execution. Stay within the configured maximum; the
        # shared circuit breaker handles politeness if YouTube rate-limits us.
        workers = max(1, min(concurrency, DEFAULT_CONFIG.max_concurrency))
        lock = threading.Lock()

        def fetch_single(video: VideoMetadata) -> VideoTranscript:
            if stop_event and stop_event.is_set():
                return VideoTranscript(
                    video_id=video.video_id,
                    title=video.title,
                    url=video.url,
                    playlist_index=video.playlist_index,
                    status="skipped",
                    error_reason="Job stopped before retrieval started",
                )

            # Politeness pacing with random jitter
            if request_delay > 0:
                time.sleep(request_delay + random.uniform(0.1, 0.4))

            transcript = self.fetcher.fetch_video_transcript(
                video,
                preferred_languages=preferred_languages,
                stop_event=stop_event,
            )
            # Immediately save to disk cache
            self.cache.save_transcript(playlist.playlist_id, transcript)
            return transcript

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_video = {}
            for v in videos_to_fetch:
                if stop_event and stop_event.is_set():
                    progress.is_cancelled = True
                    break
                fut = executor.submit(fetch_single, v)
                future_to_video[fut] = v

            for future in concurrent.futures.as_completed(future_to_video):
                v_meta = future_to_video[future]
                if stop_event and stop_event.is_set():
                    progress.is_cancelled = True
                    continue

                try:
                    transcript = future.result()
                except Exception as exc:
                    transcript = VideoTranscript(
                        video_id=v_meta.video_id,
                        title=v_meta.title,
                        url=v_meta.url,
                        playlist_index=v_meta.playlist_index,
                        status="failed",
                        error_reason=f"Worker exception: {exc}",
                    )
                    self.cache.save_transcript(playlist.playlist_id, transcript)

                with lock:
                    already_handled[transcript.video_id] = transcript
                    progress.completed += 1
                    progress.current_video_title = transcript.title
                    progress.current_video_index = transcript.playlist_index
                    progress.elapsed_seconds = time.time() - start_time

                    if transcript.is_successful():
                        progress.successful += 1
                        msg = f"✓ [{transcript.playlist_index}/{playlist.total_count}] {transcript.title[:50]} ({transcript.language})"
                    elif transcript.status == "skipped":
                        progress.skipped += 1
                        msg = f"- [{transcript.playlist_index}/{playlist.total_count}] Skipped: {transcript.title[:50]}"
                    else:
                        progress.failed += 1
                        msg = f"✗ [{transcript.playlist_index}/{playlist.total_count}] {transcript.title[:50]} - {transcript.error_reason}"

                    progress.logs.append(msg)

                if progress_callback:
                    progress_callback(progress, transcript)

        progress.is_running = False
        progress.elapsed_seconds = time.time() - start_time
        if stop_event and stop_event.is_set():
            progress.is_cancelled = True
            progress.logs.append("Processing stopped gracefully.")

        if progress_callback:
            progress_callback(progress, None)

        return self._build_result_list(playlist, already_handled)

    def _build_result_list(
        self,
        playlist: PlaylistMetadata,
        handled: Dict[str, VideoTranscript],
    ) -> List[VideoTranscript]:
        """Compile complete list of transcripts aligned with original playlist order."""
        all_cached = self.cache.load_all_transcripts(playlist.playlist_id)
        results: List[VideoTranscript] = []

        for v in playlist.videos:
            if v.video_id in handled:
                results.append(handled[v.video_id])
            elif v.video_id in all_cached:
                results.append(all_cached[v.video_id])
            else:
                results.append(
                    VideoTranscript(
                        video_id=v.video_id,
                        title=v.title,
                        url=v.url,
                        playlist_index=v.playlist_index,
                        status="skipped",
                        error_reason="Not processed in this run",
                    )
                )

        return sorted(results, key=lambda t: t.playlist_index)
