"""Streamlit web interface for the YouTube Playlist Transcript & Study Notes tool."""

import threading
from pathlib import Path
from typing import List, Optional, Set

import streamlit as st

from yt_tool.models import PlaylistMetadata, VideoTranscript, JobProgress
from yt_tool.playlist import (
    fetch_playlist_metadata,
    InvalidPlaylistURLError,
    PlaylistNotFoundError,
    PlaylistExtractionError,
)
from yt_tool.transcript import TranscriptFetcher
from yt_tool.cache import TranscriptCache
from yt_tool.network import NetworkManager, CircuitBreaker, write_netscape_cookies
from yt_tool.processor import JobManager
from yt_tool.formatter import (
    format_combined_markdown,
    format_combined_plaintext,
    sanitize_filename,
)
from yt_tool.exporter import PlaylistExporter

st.set_page_config(
    page_title="Transcript Studio",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
st.markdown(
    """
    <style>
    :root {
        --accent: #6366F1;
        --accent-2: #22D3EE;
        --surface: rgba(255, 255, 255, 0.04);
        --border: rgba(255, 255, 255, 0.09);
    }
    .block-container { padding-top: 2.2rem; max-width: 1180px; }
    .hero-title {
        font-size: 2.1rem; font-weight: 800; letter-spacing: -0.02em;
        margin: 0 0 .35rem 0; line-height: 1.15;
        background: linear-gradient(90deg, var(--accent), var(--accent-2));
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .hero-sub { color: #94A3B8; font-size: 1.02rem; margin: 0 0 1.6rem 0; }
    .card {
        background: var(--surface); border: 1px solid var(--border);
        border-radius: 14px; padding: 1.1rem 1.3rem; margin-bottom: 1rem;
    }
    div[data-testid="stMetric"] {
        background: var(--surface); border: 1px solid var(--border);
        border-radius: 12px; padding: .85rem 1rem;
    }
    div[data-testid="stMetricValue"] { font-size: 1.5rem; }
    .pill {
        display: inline-block; padding: .12rem .55rem; border-radius: 999px;
        font-size: .74rem; font-weight: 600; border: 1px solid var(--border);
        color: #CBD5E1; background: rgba(255,255,255,0.03);
    }
    .pill.ok { color: #6EE7B7; border-color: rgba(110,231,183,.35); }
    .pill.cache { color: #93C5FD; border-color: rgba(147,197,253,.35); }
    .stButton > button { border-radius: 10px; font-weight: 600; }
    .small-muted { color: #94A3B8; font-size: .85rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #
def init_session_state() -> None:
    defaults = {
        "cache": None,
        "exporter": None,
        "circuit_breaker": None,
        "playlist_metadata": None,
        "selected_video_ids": set(),
        "transcripts": {},
        "job_running": False,
        "stop_requested": False,
        "last_url": "",
        "api_key": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if st.session_state.cache is None:
        st.session_state.cache = TranscriptCache()
        st.session_state.exporter = PlaylistExporter(st.session_state.cache)
    if st.session_state.circuit_breaker is None:
        st.session_state.circuit_breaker = CircuitBreaker()


init_session_state()
cache: TranscriptCache = st.session_state.cache
exporter: PlaylistExporter = st.session_state.exporter


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### Settings")

    st.caption("LANGUAGE")
    lang_preset = st.selectbox(
        "Caption priority",
        options=[
            "English, then Hindi",
            "English only",
            "Hindi only",
            "Auto-detect (any available)",
            "Custom priority list",
        ],
        index=0,
        label_visibility="collapsed",
    )
    if lang_preset == "English, then Hindi":
        selected_langs = ["en", "hi"]
    elif lang_preset == "English only":
        selected_langs = ["en"]
    elif lang_preset == "Hindi only":
        selected_langs = ["hi"]
    elif lang_preset == "Auto-detect (any available)":
        selected_langs = ["auto"]
    else:
        custom_lang = st.text_input("Language codes in order", value="en, hi, es")
        selected_langs = [c.strip().lower() for c in custom_lang.split(",") if c.strip()]

    allow_auto_translate = st.checkbox(
        "Translate foreign captions to preferred language",
        value=True,
        help="If a video only has captions in another language, ask YouTube to translate them.",
    )
    include_timestamps = st.checkbox(
        "Include timestamps in exports",
        value=True,
    )

    st.divider()
    st.caption("SPEED & POLITENESS")
    concurrency = st.slider(
        "Parallel workers",
        min_value=1,
        max_value=4,
        value=2,
        help="Keep this low (1–2) to avoid YouTube rate limits. Higher is not always faster.",
    )
    request_delay = st.slider(
        "Delay between requests (seconds)",
        min_value=0.2,
        max_value=3.0,
        value=0.8,
        step=0.1,
        help="A small pause between videos keeps requests polite and avoids 429 errors.",
    )
    cooldown_delay = st.slider(
        "Cooldown after a rate-limit warning (seconds)",
        min_value=10,
        max_value=120,
        value=30,
        step=5,
        help="When YouTube asks us to slow down, all workers pause for this long.",
    )

    st.divider()
    with st.expander("About rate limits", expanded=False):
        st.markdown(
            "YouTube rate-limits (HTTP 429) by **IP address**. A shared hosting server's IP "
            "is throttled far more aggressively than a home connection.\n\n"
            "**If you see 429s:**\n"
            "- Wait a few minutes and press *Retry failed*.\n"
            "- Lower **Parallel workers** to 1 and raise the delay.\n"
            "- For bulk playlists, run the app locally — your own connection is much less throttled."
        )
        st.caption(
            "Hosting a public instance? Provide cookies server-side via Streamlit secrets "
            "(`youtube_cookies = \"...\"`) to reduce throttling. Never ask users to paste "
            "their personal YouTube cookies into a public app."
        )

    with st.expander("Metadata API key (optional)", expanded=False):
        st.caption(
            "Provide a free YouTube Data API v3 key to fetch playlist metadata through "
            "the official API instead of yt-dlp."
        )
        st.session_state.api_key = st.text_input(
            "YouTube Data API v3 key",
            value=st.session_state.api_key,
            type="password",
            label_visibility="collapsed",
        )

    st.divider()
    cached_playlists = cache.list_cached_playlists()
    st.caption(f"CACHE — {len(cached_playlists)} playlist(s) stored locally")
    if st.button("Clear cache", use_container_width=True):
        if st.session_state.playlist_metadata:
            cache.clear_cache(st.session_state.playlist_metadata.playlist_id)
        else:
            cache.clear_cache()
        st.session_state.transcripts = {}
        st.toast("Cache cleared.")
        st.rerun()

    st.session_state.circuit_breaker.default_cooldown = float(cooldown_delay)


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
st.markdown('<div class="hero-title">Transcript Studio</div>', unsafe_allow_html=True)
st.markdown(
    '<p class="hero-sub">Turn public YouTube playlists into clean, downloadable study notes — '
    'transcripts, Markdown handbooks, and printable bundles. Free, no account, nothing stored in the cloud.</p>',
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
in_col1, in_col2 = st.columns([5, 1])
with in_col1:
    playlist_url = st.text_input(
        "YouTube playlist URL or ID",
        placeholder="https://www.youtube.com/playlist?list=PL...",
        value=st.session_state.last_url,
        label_visibility="collapsed",
    )
with in_col2:
    fetch_btn = st.button("Fetch playlist", use_container_width=True, type="primary")

if fetch_btn:
    if not playlist_url.strip():
        st.error("Enter a YouTube playlist URL or ID first.")
    else:
        st.session_state.last_url = playlist_url.strip()
        with st.spinner("Reading playlist…"):
            try:
                meta = fetch_playlist_metadata(
                    playlist_url.strip(),
                    api_key=st.session_state.api_key or None,
                )
                st.session_state.playlist_metadata = meta
                st.session_state.selected_video_ids = {v.video_id for v in meta.videos}
                st.session_state.transcripts = cache.load_all_transcripts(meta.playlist_id)
                st.success(f"Loaded “{meta.title}” — {meta.total_count} videos.")
            except InvalidPlaylistURLError as exc:
                st.error(f"Invalid URL: {exc}")
            except PlaylistNotFoundError as exc:
                st.error(f"Playlist not found: {exc}")
            except PlaylistExtractionError as exc:
                st.error(f"Could not read playlist: {exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Unexpected error: {exc}")


# --------------------------------------------------------------------------- #
# Playlist view
# --------------------------------------------------------------------------- #
playlist: Optional[PlaylistMetadata] = st.session_state.playlist_metadata

if playlist:
    cached_count = sum(
        1 for v in playlist.videos if cache.has_successful_transcript(playlist.playlist_id, v.video_id)
    )
    total = playlist.total_count

    st.divider()
    head_l, head_m1, head_m2 = st.columns([3, 1, 1])
    with head_l:
        st.subheader(playlist.title)
        st.markdown(
            f'<span class="small-muted">Source: '
            f'<a href="{playlist.url}" target="_blank">{playlist.url}</a></span>',
            unsafe_allow_html=True,
        )
    head_m1.metric("Videos", total)
    head_m2.metric("Cached", f"{cached_count}/{total}")

    with st.expander("Choose videos", expanded=False):
        c1, c2, c3 = st.columns([1, 1, 4])
        with c1:
            if st.button("Select all", use_container_width=True):
                st.session_state.selected_video_ids = {v.video_id for v in playlist.videos}
                st.rerun()
        with c2:
            if st.button("Clear", use_container_width=True):
                st.session_state.selected_video_ids = set()
                st.rerun()
        with c3:
            st.markdown(
                f'<div style="padding-top:.5rem" class="small-muted">'
                f'{len(st.session_state.selected_video_ids)} of {total} selected</div>',
                unsafe_allow_html=True,
            )

        for video in playlist.videos:
            is_checked = video.video_id in st.session_state.selected_video_ids
            cached = cache.has_successful_transcript(playlist.playlist_id, video.video_id)
            suffix = "  ·  cached" if cached else ""
            new_val = st.checkbox(
                f"{video.playlist_index}. {video.title}{suffix}",
                value=is_checked,
                key=f"check_{video.video_id}",
            )
            if new_val:
                st.session_state.selected_video_ids.add(video.video_id)
            else:
                st.session_state.selected_video_ids.discard(video.video_id)

    st.divider()
    run_col, stop_col, retry_col = st.columns([2, 1, 2])
    with run_col:
        start_btn = st.button(
            "Get transcripts",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.job_running or not st.session_state.selected_video_ids,
        )
    with stop_col:
        stop_btn = st.button(
            "Stop",
            use_container_width=True,
            disabled=not st.session_state.job_running,
        )
    with retry_col:
        current = cache.load_all_transcripts(playlist.playlist_id)
        failed_ids = {t.video_id for t in current.values() if t.status == "failed"}
        retry_btn = st.button(
            f"Retry failed ({len(failed_ids)})",
            use_container_width=True,
            disabled=st.session_state.job_running or not failed_ids,
        )

    if stop_btn:
        st.session_state.stop_requested = True
        st.info("Stopping after the current request finishes…")

    progress_ph = st.empty()
    metrics_ph = st.empty()
    status_ph = st.empty()

    if start_btn or retry_btn:
        st.session_state.job_running = True
        st.session_state.stop_requested = False
        stop_event = threading.Event()

        selected_ids: Set[str] = st.session_state.selected_video_ids
        if retry_btn:
            selected_ids = failed_ids or selected_ids

        network_manager = NetworkManager(circuit_breaker=st.session_state.circuit_breaker)

        cookie_file = None
        try:
            secret_cookies = st.secrets.get("youtube_cookies")
        except Exception:  # noqa: BLE001 — secrets may be absent entirely
            secret_cookies = None
        if secret_cookies:
            cookie_file = str(write_netscape_cookies(secret_cookies, Path("data/.cookies.txt")))

        fetcher = TranscriptFetcher(
            network_manager=network_manager,
            allow_translation=allow_auto_translate,
            cookie_file=cookie_file,
        )
        manager = JobManager(fetcher=fetcher, cache=cache, network_manager=network_manager)

        def on_progress(prog: JobProgress, item: Optional[VideoTranscript]) -> None:
            if st.session_state.stop_requested:
                stop_event.set()
            progress_ph.progress(
                prog.progress_percentage,
                text=f"{prog.completed}/{prog.total} videos processed",
            )
            with metrics_ph.container():
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Done", f"{prog.completed}/{prog.total}")
                m2.metric("Successful", prog.successful)
                m3.metric("Failed", prog.failed)
                m4.metric("Elapsed", f"{prog.elapsed_seconds:.0f}s")
            if prog.rate_limited:
                status_ph.error(
                    "YouTube is rate-limiting this server's IP. Stopped remaining videos. "
                    "Wait a few minutes and use **Retry failed**, or run the app locally."
                )
            elif item:
                if item.is_successful():
                    status_ph.success(f"Saved: {item.title}")
                else:
                    status_ph.warning(f"Skipped: {item.title} — {item.error_reason}")

        with st.spinner("Fetching transcripts…"):
            manager.run_job(
                playlist=playlist,
                selected_video_ids=selected_ids,
                preferred_languages=selected_langs,
                concurrency=concurrency,
                request_delay=request_delay,
                stop_event=stop_event,
                progress_callback=on_progress,
                only_failed=bool(retry_btn),
            )
        st.session_state.transcripts = cache.load_all_transcripts(playlist.playlist_id)
        st.session_state.job_running = False
        st.session_state.stop_requested = False
        st.rerun()

    # ----- Results -----
    transcripts_list: List[VideoTranscript] = exporter.build_transcripts_list_from_cache(playlist)
    successful = [t for t in transcripts_list if t.is_successful()]
    failed = [t for t in transcripts_list if not t.is_successful() and t.status == "failed"]

    if successful or failed:
        st.divider()
        st.subheader("Exports")
        safe_name = sanitize_filename(playlist.title, 1, 1, extension="")
        d1, d2, d3 = st.columns(3)
        with d1:
            st.download_button(
                "Study handbook (.md)",
                data=format_combined_markdown(
                    playlist, transcripts_list, include_timestamps=include_timestamps
                ).encode("utf-8"),
                file_name=f"{safe_name}_handbook.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with d2:
            st.download_button(
                "Plain text (.txt)",
                data=format_combined_plaintext(
                    playlist, transcripts_list, include_timestamps=include_timestamps
                ).encode("utf-8"),
                file_name=f"{safe_name}_notes.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with d3:
            st.download_button(
                "Full bundle (.zip)",
                data=exporter.generate_zip_bytes(
                    playlist, transcripts_list, include_timestamps=include_timestamps
                ),
                file_name=f"{safe_name}_transcripts.zip",
                mime="application/zip",
                use_container_width=True,
            )

        tab_ok, tab_fail = st.tabs([f"Successful ({len(successful)})", f"Issues ({len(failed)})"])
        with tab_ok:
            if not successful:
                st.info("No transcripts yet. Run the fetcher above.")
            for t in successful:
                with st.expander(f"{t.playlist_index}. {t.title}  ·  {t.language}"):
                    st.caption(f"{t.url}  ·  {len(t.segments)} segments")
                    st.text_area(
                        "Preview",
                        (t.full_text[:1200] + "…") if len(t.full_text) > 1200 else t.full_text,
                        height=140,
                        key=f"prev_{t.video_id}",
                        label_visibility="collapsed",
                    )
        with tab_fail:
            if not failed:
                st.success("Every processed video was transcribed.")
            for t in failed:
                st.markdown(f"- **{t.playlist_index}. {t.title}** — `{t.error_reason}`")

st.divider()
st.caption(
    "Transcript Studio · Built with Streamlit, yt-dlp & youtube-transcript-api. "
    "Only public captions are retrieved — no video or audio is downloaded. "
    "Please respect creators' rights and YouTube's Terms of Service."
)
