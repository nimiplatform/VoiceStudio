import os
import uuid
import asyncio
import logging
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
import soundfile as sf
from typing import Optional
from fastapi import Request
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from core.db import db_conn
from core.config import PREVIEW_DIR
from core.tasks import task_manager
from core.logging_utils import log_safe
from core import event_bus
from schemas.requests import DubIngestUrlRequest, ParseSubtitleTextRequest
from services.ffmpeg_utils import find_ffmpeg
from services.segmentation import (
    segment_transcript,
    assign_speakers_from_diarization,
    assign_speakers_from_turns,
    assign_speakers_heuristic,
    resplit_segments_by_diarization,
    resplit_segments_by_turns,
    _words_from_whisper,
    clean_up_segments,
)
from services import dub_pipeline

router = APIRouter()
logger = logging.getLogger("omnivoice.api")

_MAX_COOKIE_EXPORT_BYTES = 1024 * 1024


def _cookie_transport_allowed(
    scheme: str, client_host: str | None, origin: str | None
) -> bool:
    """Credentials may cross HTTP only from a local UI to a loopback peer."""
    from api.dependencies import is_local_host

    if scheme == "https":
        return True
    try:
        origin_host = urlsplit(origin or "").hostname or ""
    except ValueError:
        return False
    return is_local_host(client_host or "") and (
        is_local_host(origin_host) or origin_host == "tauri.localhost"
    )


def _stage_cookie_export(contents: str | None) -> str | None:
    """Write an explicitly supplied cookies.txt export to a private temp file."""
    if contents is None:
        return None
    cookie_bytes = contents.encode("utf-8")
    if len(cookie_bytes) > _MAX_COOKIE_EXPORT_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cookie file is too large (maximum 1 MB). Export cookies in "
                "Netscape cookies.txt format and try again."
            ),
        )
    first_line = contents.lstrip("\ufeff\r\n ").splitlines()[0] if contents.strip() else ""
    if not first_line.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        raise HTTPException(
            status_code=400,
            detail=(
                "This is not a Netscape cookies.txt export. Export cookies as "
                "cookies.txt from your browser, then choose that file."
            ),
        )
    fd, cookie_path = tempfile.mkstemp(
        prefix="voicestudio-ytdlp-", suffix=".cookies.txt",
    )
    try:
        os.chmod(cookie_path, 0o600)
        with os.fdopen(fd, "wb") as cookie_handle:
            cookie_handle.write(cookie_bytes)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass  # Best effort: fdopen may already have consumed/closed the descriptor.
        try:
            os.unlink(cookie_path)
        except OSError:
            pass  # Best effort: preserve the original staging error.
        raise
    return cookie_path


# ── Legacy-name aliases to services/dub_pipeline.py ────────────────────────
# Phase 2.4 moved the business logic into a service. Other routers
# (dub_generate, dub_translate, dub_export) + internal call sites below still
# reference the `_get_job` / `_save_job` / `_active_procs` names; those
# aliases let the transition happen without a repo-wide rename pass.
#
# New code should import from `services.dub_pipeline` directly. Aliases can
# disappear once every caller updates.
_dub_jobs           = dub_pipeline._dub_jobs
_active_procs       = dub_pipeline._active_procs
_active_procs_lock  = dub_pipeline._active_procs_lock
_DUB_DIR_REAL       = dub_pipeline._DUB_DIR_REAL

_compute_file_hash = dub_pipeline.compute_file_hash
_find_cached_job   = dub_pipeline.find_cached_job
_safe_job_dir      = dub_pipeline.safe_job_dir
_register_proc     = dub_pipeline.register_proc
_unregister_proc   = dub_pipeline.unregister_proc
_kill_job_procs    = dub_pipeline.kill_job_procs
_get_job           = dub_pipeline.get_job
_save_job          = dub_pipeline.save_job

# Pasted subtitle text is a transcript, not a media file: a feature-length
# film's .srt is ~150 KB. 2 MB of characters is ~13x the worst realistic case
# and still cheap to regex — past that we refuse rather than let a stray
# paste (or a mis-aimed binary) burn CPU in the parser.
_MAX_SUBTITLE_PASTE_CHARS = 2_000_000

_SRT_REPLACED_FIELDS = {
    "id",
    "start",
    "end",
    "text",
    "text_original",
    "translations",
    "translate_error",
    "translate_degraded",
}


def _best_overlapping_segment(cue: dict, existing: list[dict]) -> dict | None:
    """Return the prior segment with the strongest temporal overlap."""
    cue_start = float(cue.get("start") or 0.0)
    cue_end = float(cue.get("end") or cue_start)
    cue_mid = (cue_start + cue_end) / 2.0
    best = None
    best_key = None
    for index, segment in enumerate(existing):
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        overlap = min(cue_end, end) - max(cue_start, start)
        if overlap <= 0:
            continue
        midpoint_distance = abs(cue_mid - ((start + end) / 2.0))
        key = (overlap, -midpoint_distance, -index)
        if best_key is None or key > best_key:
            best = segment
            best_key = key
    return best


def _carry_srt_voice_metadata(
    cues: list[dict],
    existing: list[dict],
    segment_clones: dict | None,
    speaker_clones: dict | None = None,
) -> tuple[list[dict], dict]:
    """Replace subtitle content while retaining the source cast assignment."""
    source_clones = dict(segment_clones or {})
    source_speaker_clones = dict(speaker_clones or {})
    # Replacement cues get new positional ids. Starting from the old map would
    # let an unmatched cue whose new id happens to equal an old id inherit an
    # unrelated reference. Only explicitly overlap-matched references survive.
    clones = {}
    merged_segments = []
    for new_id, cue in enumerate(cues):
        prior = _best_overlapping_segment(cue, existing)
        metadata = {
            key: value
            for key, value in (prior or {}).items()
            if key not in _SRT_REPLACED_FIELDS
        }
        merged = {
            **metadata,
            "id": new_id,
            "start": cue.get("start", 0.0),
            "end": cue.get("end", 0.0),
            "text": cue.get("text", ""),
            "text_original": cue.get("text", ""),
        }
        if not merged.get("speaker_id"):
            merged["speaker_id"] = cue.get("speaker_id") or "Speaker 1"
        if prior is not None:
            prior_id = str(prior.get("id", ""))
            clone = source_clones.get(prior_id)
            if clone is None:
                clone = source_speaker_clones.get(prior.get("speaker_id"))
            if clone is not None:
                clones[str(new_id)] = clone
                if merged.get("profile_id") == f"auto-seg:{prior_id}":
                    merged["profile_id"] = f"auto-seg:{new_id}"
        merged_segments.append(merged)
    return merged_segments, clones


@router.post("/dub/parse-subtitle-text")
def dub_parse_subtitle_text(req: ParseSubtitleTextRequest):
    """Parse pasted subtitle text into timed cues. Stateless — no job, no I/O.

    A thin wrapper over `services.srt_parser.parse_srt` so the client's
    "paste a translation" flow reuses the exact lenient parser the .srt
    import path uses (BOM / CRLF / `.`-vs-`,` ms / missing indices, plus
    de-overlapping). Unlike `/dub/import-srt/{job_id}` this mutates
    nothing: the caller maps these cues onto the segments it already has,
    keeping the existing timings and `text_original`.
    """
    text = req.text or ""
    if len(text) > _MAX_SUBTITLE_PASTE_CHARS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Pasted text is too large ({len(text)} characters). "
                f"Limit is {_MAX_SUBTITLE_PASTE_CHARS} characters."
            ),
        )

    from services.srt_parser import parse_srt
    result = parse_srt(text)
    if not result.segments:
        raise HTTPException(
            status_code=400,
            detail=(
                "No timed cues found in the pasted text. "
                f"Skipped {result.skipped_cues} malformed cue(s). "
                "Expected timestamp lines like '00:00:01,000 --> 00:00:04,500'."
            ),
        )
    return {
        "segments": [
            {"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in result.segments
        ],
        "skipped_cues": result.skipped_cues,
        "dropped_overlaps": result.dropped_overlaps,
    }


@router.post("/dub/import-srt/{job_id}")
async def dub_import_srt(job_id: str, file: UploadFile = File(...)):
    """Replace `job["segments"]` with timestamps + text parsed from an SRT
    file. Used as a fallback when Whisper mis-transcribes — the user can
    point at their own pre-synced subtitles and skip ASR entirely.

    Returns the new segment list plus counts of any cues we had to skip or
    re-time (overlap shifts). The caller surfaces these so the user knows
    if the import wasn't lossless.
    """
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        raw_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read uploaded file: {e}") from e
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded SRT file is empty.")
    # Most SRT files are UTF-8 (with or without BOM); fall back to latin-1
    # so legacy Windows-encoded subs don't blow up the import.
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1", errors="replace")

    from services.srt_parser import parse_srt
    result = parse_srt(text)
    if not result.segments:
        raise HTTPException(
            status_code=400,
            detail=(
                "No valid cues found in the uploaded file. "
                f"Skipped {result.skipped_cues} malformed cue(s). "
                "Expected SubRip (.srt) format: index, then 'HH:MM:SS,ms --> HH:MM:SS,ms', then text, blank line."
            ),
        )

    # Clamp cues that run past the source media's known duration. Pipeline
    # downstream code assumes segment.end <= duration; without this, dub
    # generation would try to time-stretch into negative slack.
    duration = float(job.get("duration") or 0.0)
    clamped = 0
    if duration > 0:
        kept = []
        for seg in result.segments:
            if seg["start"] >= duration:
                continue
            if seg["end"] > duration:
                seg = {**seg, "end": round(duration, 3)}
                clamped += 1
            kept.append(seg)
        # Re-id after clamp drops.
        segments = [{**s, "id": i} for i, s in enumerate(kept)]
    else:
        segments = result.segments

    prior_segments = [
        segment for segment in (job.get("segments") or []) if isinstance(segment, dict)
    ]
    segments, segment_clones = _carry_srt_voice_metadata(
        segments,
        prior_segments,
        job.get("segment_clones"),
        job.get("speaker_clones"),
    )
    job["segments"] = segments
    job["segment_clones"] = segment_clones
    # A pooled speaker clone is keyed only by a display label. Replacement
    # cues can reuse that label without overlapping the original speaker, so
    # retain matched pooled references as segment-specific clones above and
    # drop the global map before rebuilding the cast.
    job["speaker_clones"] = {}
    if segment_clones:
        from services.speaker_clone import build_cast_sources

        job["cast_sources"] = build_cast_sources(
            segments,
            None,
            segment_clones,
        )
    else:
        job.pop("cast_sources", None)
    # `source_lang` stays whatever the user (or the upload step) set; we
    # don't try to language-detect off the cue text — that's noisy and the
    # user usually knows what their .srt is.
    _save_job(job_id, job)
    logger.info(
        "Imported %d cue(s) from .srt for job %s (skipped=%d, overlap_shifted=%d, clamped=%d)",
        len(segments), log_safe(job_id), result.skipped_cues, result.dropped_overlaps, clamped,
    )
    return {
        "segments": segments,
        "stats": {
            "imported": len(segments),
            "skipped_malformed": result.skipped_cues,
            "dropped_overlap": result.dropped_overlaps,
            "clamped_to_duration": clamped,
        },
    }


def _select_downloaded_caption_track(
    tracks: dict[str, list[dict]], preferred: str | None,
) -> str | None:
    """Choose the closest original-language caption track deterministically."""
    available = [key for key, cues in tracks.items() if isinstance(cues, list) and cues]
    if not available:
        return None
    preferred_tag = (preferred or "").strip().lower().replace("_", "-")
    preferred_base = preferred_tag.split("-", 1)[0]

    def rank(key: str) -> tuple[int, int, int, str]:
        tag = key.strip().lower().replace("_", "-")
        base = tag.split("-", 1)[0]
        if preferred_tag:
            language_rank = 0 if tag == preferred_tag else 1 if base == preferred_base else 2
        else:
            language_rank = 0
        return (
            language_rank,
            0 if tag.endswith("-orig") else 1,
            0 if "-" not in tag else 1,
            tag,
        )

    return min(available, key=rank)


def _prepare_downloaded_caption_segments(cues: list[dict], duration: float) -> list[dict]:
    """Normalize downloaded VTT cues into safe, sequential Dub segments."""
    def cue_start(cue: dict) -> float:
        try:
            return float(cue.get("start") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def remove_repeated_prefix(previous: str, current: str) -> str:
        previous_words = previous.split()
        current_words = current.split()
        folded_previous = [word.casefold() for word in previous_words]
        folded_current = [word.casefold() for word in current_words]
        for count in range(min(len(previous_words), len(current_words)), 0, -1):
            if folded_previous[-count:] == folded_current[:count]:
                return " ".join(current_words[count:])
        return current

    prepared: list[dict] = []
    previous_end = 0.0
    ordered = sorted((cue for cue in cues if isinstance(cue, dict)), key=cue_start)
    for index, cue in enumerate(ordered):
        try:
            raw_start = max(0.0, float(cue.get("start") or 0.0))
            end = float(cue.get("end") or raw_start)
        except (TypeError, ValueError):
            continue
        text = " ".join(str(cue.get("text") or "").split())
        if duration > 0:
            if raw_start >= duration:
                continue
            end = min(end, duration)
        if prepared and raw_start < previous_end:
            text = remove_repeated_prefix(prepared[-1]["text"], text)
            if not text:
                prepared[-1]["end"] = round(max(previous_end, end), 3)
                previous_end = max(previous_end, end)
                continue
        # Caption hosts commonly emit slightly overlapping cues. Dubbing needs
        # a monotonic timeline, so trim the later cue rather than manufacture
        # overlapping speech slots.
        start = max(raw_start, previous_end)
        if not text or end <= start:
            continue
        prepared.append({
            "id": str(index),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "speaker_id": "Speaker 1",
        })
        previous_end = end

    cleaned = clean_up_segments(prepared)
    return [
        {
            **segment,
            "id": index,
            "text_original": segment.get("text", ""),
        }
        for index, segment in enumerate(cleaned)
    ]


@router.post("/dub/use-downloaded-captions/{job_id}")
def dub_use_downloaded_captions(job_id: str):
    """Seed a prepared Dub job from its downloaded caption track."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    tracks = job.get("youtube_subs")
    if not isinstance(tracks, dict):
        raise HTTPException(status_code=404, detail="No downloaded captions are available")
    caption_lang = _select_downloaded_caption_track(
        tracks,
        job.get("source_lang_override") or job.get("source_lang"),
    )
    if caption_lang is None:
        raise HTTPException(status_code=404, detail="No downloaded captions are available")
    segments = _prepare_downloaded_caption_segments(
        tracks[caption_lang],
        float(job.get("duration") or 0.0),
    )
    if not segments:
        raise HTTPException(status_code=422, detail="Downloaded captions contain no usable cues")

    source_lang = job.get("source_lang_override") or _detected_source_lang(caption_lang)
    job["segments"] = segments
    job["source_lang"] = source_lang
    job["full_transcript"] = " ".join(segment["text"] for segment in segments)
    # Caption files contain timing and text, but no trustworthy speaker or
    # reference-audio attribution. Never retain stale clone maps from a prior
    # transcript on the same job.
    job["segment_clones"] = {}
    job["speaker_clones"] = {}
    job.pop("cast_sources", None)
    _save_job(job_id, job)
    return {
        "segments": segments,
        "source_lang": source_lang,
        "caption_lang": caption_lang,
        "available": sorted(tracks.keys()),
    }


@router.post("/dub/cleanup-segments/{job_id}")
def dub_cleanup_segments(job_id: str):
    """Re-run merge/stitch passes on a job's existing segments to drop fragments."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    segments = job.get("segments") or []
    cleaned = clean_up_segments(segments)
    job["segments"] = cleaned
    _save_job(job_id, job)
    return {"segments": cleaned, "before": len(segments), "after": len(cleaned)}


@router.post("/dub/abort/{job_id}")
def dub_abort(job_id: str):
    """Cancel in-flight upload/transcribe subprocesses for a job."""
    with _active_procs_lock:
        had_procs = bool(_active_procs.get(job_id))
    _kill_job_procs(job_id)
    try:
        had_task = task_manager.cancel_task(job_id)
    except Exception as exc:
        logger.warning("Dub task cancellation failed")
        raise HTTPException(
            status_code=503,
            detail="The dub could not be fully aborted. Retry the abort operation.",
        ) from exc
    job = _dub_jobs.get(job_id)
    if job is not None:
        job["aborted"] = True
    # Cancellation is idempotent: a missing active task means it already
    # stopped between the renderer aborting its stream and this request.
    return {
        "aborted": True,
        "had_active_procs": had_procs,
        "had_active_task": had_task,
    }


@router.get("/dub/history")
def list_dub_history():
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM dub_history ORDER BY created_at DESC LIMIT 30").fetchall()
    return [dict(r) for r in rows]

@router.delete("/dub/history")
def clear_dub_history():
    """Delete persisted dub rows and their on-disk dirs (scoped to known IDs)."""
    with db_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM dub_history").fetchall()]

    def _delete_rows():
        with db_conn() as conn:
            conn.execute("DELETE FROM dub_history")

    # Row-delete + in-memory evict together, so an ingest finishing right now
    # can't re-save a job the user just cleared (#1252 review). This path
    # never evicted from memory at all before, so an in-flight job survived
    # "clear history" outright.
    dub_pipeline.purge_jobs(ids, delete_rows=_delete_rows, include_inflight=True)
    for jid in ids:
        safe = _safe_job_dir(jid)
        if safe and os.path.isdir(safe):
            shutil.rmtree(safe, ignore_errors=True)
    event_bus.emit("dub_history")
    return {"cleared": True, "count": len(ids)}

@router.delete("/dub/history/{history_id}")
def delete_single_dub_history(history_id: str):
    def _delete_row():
        with db_conn() as conn:
            conn.execute("DELETE FROM dub_history WHERE id=?", (history_id,))

    # #1331 (deletion half): the content-hash cache points newer jobs' paths
    # (vocals, and pre-fix clone refs) into this dir. Check BEFORE the row is
    # deleted — the scan reads dub_history, and after _delete_row this row's
    # neighbours are all that's left to consult either way.
    holders = dub_pipeline.job_dir_referenced_by_others(history_id)

    # Atomic with the evict — see purge_jobs (#1252 review).
    dub_pipeline.purge_jobs([history_id], delete_rows=_delete_row)
    safe = _safe_job_dir(history_id)
    if holders:
        # Keep the directory: another saved dub still renders from files in
        # it. Disk is the cheap thing here; a job that silently loses its
        # cloned voice on every regen is not. The row is gone, so the entry
        # disappears from history either way.
        logger.info(
            "dub delete %s: history row removed but directory kept — still "
            "referenced by job(s) %s (#1331)", log_safe(history_id), log_safe(", ".join(holders)),
        )
    elif safe and os.path.isdir(safe):
        shutil.rmtree(safe, ignore_errors=True)
    event_bus.emit("dub_history", {"action": "deleted", "id": history_id})
    return {"deleted": True, "dir_kept_for": holders}

@router.post("/preview/upload")
async def preview_upload(video: UploadFile = File(...)):
    ext = os.path.splitext(video.filename or "video.mp4")[1].lower()
    safe_name = f"{uuid.uuid4().hex[:12]}"
    vid_path = os.path.join(PREVIEW_DIR, f"{safe_name}{ext}")
    wav_path = os.path.join(PREVIEW_DIR, f"{safe_name}.wav")
    payload = await video.read()

    def _write_and_extract() -> bool:
        with open(vid_path, "wb") as f:
            f.write(payload)
        if ext in {".wav", ".mp3", ".m4a", ".aac"}:
            return False
        try:
            ffmpeg_cmd = [
                find_ffmpeg(), "-y", "-i", vid_path,
                "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                wav_path
            ]
            subprocess.run(
                ffmpeg_cmd, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=300,
            )
            return True
        except Exception as e:
            logger.warning("FFmpeg extraction failed: %s", log_safe(e))
            return False

    # File writes and ffmpeg are blocking operations. Keep them on the bounded
    # CPU pool so a large preview cannot stall unrelated API requests (#1667).
    has_audio = await asyncio.get_running_loop().run_in_executor(
        None, _write_and_extract
    )

    return {
        "url": f"/preview/{safe_name}{ext}",
        "audioUrl": f"/preview/{safe_name}.wav" if has_audio else f"/preview/{safe_name}{ext}",
        "filename": video.filename,
    }

@router.get("/preview/{filename}")
async def preview_serve(filename: str):
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, "Invalid preview filename")
    preview_real = os.path.realpath(PREVIEW_DIR)
    path = os.path.realpath(os.path.join(PREVIEW_DIR, filename))
    if not path.startswith(preview_real + os.sep):
        raise HTTPException(400, "Invalid preview filename")
    if not os.path.isfile(path):
        raise HTTPException(404, "Preview not found")
    ext = os.path.splitext(filename)[1].lower()
    media_types = {
        ".mp4": "video/mp4", ".mov": "video/quicktime", 
        ".mkv": "video/x-matroska", ".webm": "video/webm", 
        ".avi": "video/x-msvideo", ".wav": "audio/wav", 
        ".mp3": "audio/mpeg"
    }
    return FileResponse(path, media_type=media_types.get(ext, "application/octet-stream"))

# ── Legacy aliases for the extracted ingest pipeline (Phase 2.4 finish) ────
_run_proc_factory = dub_pipeline.run_proc_factory
_yt_download_sync = dub_pipeline.yt_download_sync
_prep_event       = dub_pipeline.prep_event
_ingest_gen       = dub_pipeline.ingest_pipeline


#: Recognised audio extensions for audio-only dubbing (#119). When the client
#: declares input_type=audio we refuse anything that isn't a known audio
#: container so a mislabelled video can't slip past the video-skipping branch.
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}

# Source-language choices exposed by the first-party dub UI, plus every
# language code Whisper can write back after auto-detection. A restored job
# may reuse that detected value as the next upload's override, so rejecting our
# own persisted codes strands otherwise valid dubbing sessions (#1737).
# Keeping this an allow-list still rejects language names and private-use
# BCP-47 tags. Values are normalized to lowercase below.
_DUB_SOURCE_LANG_CODES = frozenset({
    "af", "sq", "am", "ar", "hy", "az", "eu", "be", "bn", "bs", "bg",
    "my", "ca", "cmn-hans", "cmn-hant", "hr", "cs", "da", "nl", "en",
    "et", "fi", "fr", "gl", "ka", "de", "el", "gu", "ht", "ha", "haw",
    "he", "hi", "hu", "is", "id", "it", "ja", "jw", "kn", "kk", "km",
    "ko", "ku", "ky", "lo", "la", "lv", "lt", "mk", "ms", "ml", "mt",
    "mi", "mr", "mn", "ne", "no", "ps", "fa", "pl", "pt", "pa", "ro",
    "ru", "sm", "gd", "sr", "sn", "sd", "si", "sk", "sl", "so", "es",
    "su", "sw", "sv", "tg", "ta", "te", "th", "tr", "uk", "ur", "uz",
    "vi", "cy", "xh", "yi", "yo", "zu",
    "as", "ba", "bo", "br", "fo", "lb", "ln", "mg", "nn", "oc", "sa",
    "tk", "tl", "tt", "yue", "zh",
})


def _source_lang_override(value: str | None) -> str | None:
    """Normalize a user-selected source language; auto/und means detect.

    A rejection NAMES the code it rejected. "Invalid source language code" on
    its own cannot be acted on or reported usefully: it does not say which of
    the ninety-odd codes was wrong, so neither the user nor a maintainer
    reading the auto-filed issue can tell whether the picker offered something
    the backend does not accept, or a stale preference from an older build is
    still being sent (#1960).

    The value is a language code the user chose from a menu — not private
    data — and the neighbouring engine validator already echoes its input the
    same way.
    """
    code = (value or "").strip().lower()
    if code in {"", "auto", "und"}:
        return None
    if code not in _DUB_SOURCE_LANG_CODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid source language code: {code!r}. Pick a language from "
                "the Dubbing source-language menu, or leave it on auto-detect."
            ),
        )
    return code


def _detected_source_lang(value: str | None) -> str:
    """Normalize an ASR language without truncating valid three-letter codes."""
    code = (value or "en").split("_", 1)[0].strip().lower()
    if code in _DUB_SOURCE_LANG_CODES:
        return code
    short = code[:2]
    return short if short in _DUB_SOURCE_LANG_CODES else "en"


@router.post("/dub/upload")
async def dub_upload(
    video: UploadFile = File(...),
    job_id: Optional[str] = Form(None),
    input_type: str = Form("video"),
    source_lang: Optional[str] = Form(None),
):
    """Accept a media upload, write to disk, queue background prep task.

    `input_type` is "video" (default) or "audio". Audio-only jobs (#119) skip
    scene detection, thumbnailing, and the final video mux — the transcribe →
    translate → TTS core is identical.

    Returns 202 with {job_id, task_id, filename}. Client should open SSE on
    /tasks/stream/{task_id} to monitor extract/demucs stages and wait for the
    'ready' event before starting transcription.
    """
    input_type = (input_type or "video").lower()
    if input_type not in ("video", "audio"):
        raise HTTPException(status_code=400, detail="input_type must be 'video' or 'audio'")

    job_id = job_id or str(uuid.uuid4())[:8]
    job_dir = _safe_job_dir(job_id)
    if job_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid job_id. Must be alphanumeric + hyphens/underscores only, ≤64 chars. Generate a fresh job_id or omit it to auto-create one.",
        )
    ext = os.path.splitext(video.filename or "video.mp4")[1]
    if input_type == "audio" and ext.lower() not in _AUDIO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Audio-only dubbing needs an audio file ({', '.join(sorted(_AUDIO_EXTS))}); got '{ext or 'no extension'}'.",
        )

    source_lang_override = _source_lang_override(source_lang)
    os.makedirs(job_dir, exist_ok=True)

    video_path = os.path.join(job_dir, f"original{ext}")

    def _stream_upload_to_disk() -> None:
        # UploadFile is already a spooled file. Copy it in bounded chunks on a
        # worker thread instead of materialising a multi-GB video in RAM and
        # blocking every API request while the event loop writes it.
        video.file.seek(0)
        with open(video_path, "wb") as output:
            shutil.copyfileobj(video.file, output, length=1024 * 1024)

    try:
        await asyncio.to_thread(_stream_upload_to_disk)
    finally:
        await video.close()

    filename = video.filename or f"video{ext}"
    task_id = f"prep_{job_id}"
    await task_manager.add_task(
        task_id, "prep",
        _ingest_gen, job_id, job_dir,
        {
            "kind": "file",
            "path": video_path,
            "input_type": input_type,
            "source_lang": source_lang_override,
        },
        filename,
    )
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "task_id": task_id, "filename": filename},
    )


@router.post("/dub/ingest-url")
async def dub_ingest_url(req: DubIngestUrlRequest, request: Request):
    """Ingest a remote video URL via yt-dlp. Queues background prep task.

    Returns 202 immediately with {job_id, task_id}. All work (download,
    audio extract, Demucs, scene detect, thumbnail) happens in the background
    task and progress is streamed via /tasks/stream/{task_id}.
    """
    url = (req.url or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail="URL must start with http:// or https://. Paste a full video link (e.g. https://youtube.com/watch?v=…) or drop a local file instead.",
        )
    source_lang_override = _source_lang_override(req.source_lang)

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="URL ingest needs yt-dlp, but it isn't installed. Install it (`pip install yt-dlp`) and restart the server — or drop a local video file instead.",
        )

    job_id = req.job_id or str(uuid.uuid4())[:8]
    job_dir = _safe_job_dir(job_id)
    if job_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid job_id. Must be alphanumeric + hyphens/underscores only, ≤64 chars. Generate a fresh job_id or omit it to auto-create one.",
        )
    if req.cookie_file and not _cookie_transport_allowed(
        request.url.scheme,
        request.client.host if request.client else None,
        request.headers.get("origin"),
    ):
        raise HTTPException(
            status_code=403,
            detail="Cookie exports require HTTPS or the local desktop app.",
        )
    os.makedirs(job_dir, exist_ok=True)
    cookie_path = _stage_cookie_export(req.cookie_file)

    task_id = f"prep_{job_id}"
    source = {
        "kind": "url",
        "url": url,
        "fetch_subs": bool(req.fetch_subs),
        "sub_langs": req.sub_langs or None,
        "cookie_file": cookie_path,
        "source_lang": source_lang_override,
    }
    try:
        await task_manager.add_task(
            task_id, "prep",
            _ingest_gen, job_id, job_dir,
            source, None,
        )
    except Exception:
        if cookie_path:
            try:
                os.unlink(cookie_path)
            except OSError:
                pass  # Best effort: do not hide the task-enqueue failure.
        raise
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "task_id": task_id, "filename": ""},
    )


TRANSCRIBE_CHUNK_S = float(os.environ.get("OMNIVOICE_TRANSCRIBE_CHUNK_S", "30.0"))
TRANSCRIBE_CHUNK_TIMEOUT_S = float(os.environ.get("OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S", "120.0"))
#: How many times to attempt each transcribe chunk before giving up on it. A
#: transient wedge (esp. the first chunk, where whisperx cold-loads its model)
#: shouldn't silently drop that whole window — retry once on a fresh pool so the
#: transcript doesn't come back "missing the beginning".
_CHUNK_TRANSCRIBE_ATTEMPTS = max(1, int(os.environ.get("OMNIVOICE_TRANSCRIBE_CHUNK_ATTEMPTS", "2")))
#: Seconds between SSE keepalive comments while the transcribe preflight loads
#: the ASR backend (#1196). A first-run load can download multi-GB weights —
#: minutes with zero bytes on the wire — and byte-silent streams get severed
#: by Chrome's ~5 min no-response cap and by reverse-proxy idle timeouts,
#: which the UI can only report as the generic "stream dropped" guess.
ASR_LOAD_KEEPALIVE_S = float(os.environ.get("OMNIVOICE_ASR_LOAD_KEEPALIVE_S", "15.0"))


_sse_event = dub_pipeline.sse_event
_prep_event_helper = dub_pipeline.prep_event  # alias; we keep the module-local _prep_event below for the inline one-liner shape

#: User-facing warning emitted when auto voice cloning is skipped because the
#: speaker labels came from the silence-gap heuristic (see _diarize /
#: extract_speaker_clones — gap-based labels routinely mix two people's audio
#: into one reference, which is how "made up" clone voices happen).
CLONE_SKIP_HEURISTIC_MSG = (
    "auto voice cloning skipped: speaker labels are gap-based estimates — "
    "set up diarization (Model Catalogue → Other weights → pyannote) for per-speaker clones"
)


def _clamp_num_speakers(value) -> Optional[int]:
    """Clamp the user's speaker-count hint to a sane 1–20 range.

    Shared by the SSE and legacy transcribe endpoints so the two can't drift.
    None / non-int / out-of-range → None (auto-detect), so a bad query string
    can never break a diarization call.
    """
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 20 else None
