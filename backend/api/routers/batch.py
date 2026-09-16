"""Batch dubbing queue — POST videos with settings, process sequentially.

This is a lightweight batch orchestrator. Each job is a dub project that
runs through the same ingest→transcribe→translate→generate pipeline as
a manual dub, but driven by the queue instead of the UI.

The queue is in-memory (lives for the process lifetime). Jobs persist to
the SQLite `jobs` table for history, but the queue itself restarts empty
on backend restart — intentional, since GPU jobs can't be safely resumed.
"""
import os
import json
import shutil
import uuid
import time
import asyncio
import logging
from typing import Optional, List

from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.config import DATA_DIR
from core import failure
from core.logging_utils import log_safe
from core.file_cleanup import FileCleanupError, unlink_if_present

router = APIRouter()
_running_tasks: dict[str, asyncio.Task] = {}
logger = logging.getLogger("omnivoice.batch")

# Compatibility values emitted by the established Tauri Batch picker. They
# are taxonomy tokens, not arbitrary prose, and are resolved server-side so
# native watch-folder uploads and both desktop clients use the same voice.
_BATCH_PRESET_INSTRUCT = {
    "narrator": "male, middle-aged, low pitch, british accent",
    "excited_child": "child, high pitch",
    "anxious_whisper": "young adult, whisper",
    "surprised_woman": "female, young adult, high pitch",
    "elderly_story": "male, elderly, very low pitch",
    "sichuan": "female, young adult, moderate pitch, \u56db\u5ddd\u8bdd",
}

# ── In-memory queue ─────────────────────────────────────────────────────

_queue: asyncio.Queue = None       # Lazily initialised
_worker_task: asyncio.Task = None  # Background consumer
_processing_job_ids: set[str] = set()
_jobs: dict = {}                   # job_id → status dict


class BatchJobStatus(BaseModel):
    id: str
    status: str  # "queued" | "running" | "done" | "failed" | "cancelled"
    filename: str
    langs: List[str]
    voice_id: Optional[str] = None
    preserve_bg: bool = True
    translation_provider: Optional[str] = None
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    progress: Optional[dict] = None
    attempts: int = 1
    retry_ready: bool = True
    setup_required: Optional[dict] = None


def _ensure_queue():
    """Lazy-init the asyncio queue + worker on first use."""
    global _queue, _worker_task
    if _queue is None:
        _queue = asyncio.Queue()
        _worker_task = asyncio.ensure_future(_worker())


async def _worker():
    """Process jobs one at a time from the queue."""
    while True:
        job_id = await _queue.get()
        job = _jobs.get(job_id)
        if not job or job["status"] == "cancelled":
            _queue.task_done()
            continue

        job["status"] = "running"
        job["started_at"] = time.time()
        _processing_job_ids.add(job_id)
        logger.info("Batch job %s starting: %s", job_id, job["filename"])

        try:
            from business.batch import run_batch
            runner = asyncio.create_task(run_batch(job_id, job))
            _running_tasks[job_id] = runner
            await runner
            if job["status"] != "cancelled":
                job["status"] = "done"
                job["finished_at"] = time.time()
                logger.info(
                    "Batch job %s completed in %.1fs",
                    job_id, job["finished_at"] - job["started_at"],
                )
        except asyncio.CancelledError:
            # Task cancellation always means SHUTDOWN: the job-level cancel
            # endpoint only flips job["status"] — nothing ever cancels this
            # task to abort a single job. Swallowing the CancelledError here
            # made the worker unkillable (the while-loop re-entered
            # _queue.get() and event-loop teardown hung forever in
            # _cancel_all_tasks waiting on a task that never finishes). Mark
            # the in-flight job, then let the cancellation propagate.
            user_cancelled = job['status'] == 'cancelled'
            job['status'] = 'cancelled'
            job['finished_at'] = time.time()
            if not user_cancelled:
                raise
        except Exception as e:
            job["status"] = "failed"
            # plan-04 (#131): guaranteed non-empty, structured reason.
            job["error"] = failure.build_failure(e, stage="batch", include_diagnostic=False)["reason"]
            job["finished_at"] = time.time()
            logger.error("Batch job %s failed: %s", job_id, e, exc_info=True)
        finally:
            _running_tasks.pop(job_id, None)
            _processing_job_ids.discard(job_id)
            if job["status"] == "cancelled":
                job["retry_ready"] = True
            _queue.task_done()


def _set_progress(job, stage, percent=0, **extra):
    """Update a job's progress dict."""
    job["progress"] = {"stage": stage, "percent": percent, **extra}


#: Override for the native dub batch width. Set to 1 to disable batching.

#: Hard ceiling on the override — a batch this wide is already amortizing
#: almost all of the per-call setup, and beyond it the failure mode is an OOM
#: that costs more than the saving.

# Bound each allocation while persisting multipart uploads. Video inputs can
# be many gigabytes; `await UploadFile.read()` with no size used to mirror the
# entire file in process memory before writing it back out.
_UPLOAD_CHUNK_BYTES = 1024 * 1024

_REMOTE_BATCH_OPERATION = "batch_segments"






async def _save_upload(upload: UploadFile, destination: str) -> None:
    try:
        with open(destination, "wb") as output:
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                output.write(chunk)
    except BaseException:
        try:
            unlink_if_present(destination)
        except FileCleanupError:
            logger.warning("Could not remove incomplete batch upload", exc_info=True)
        raise


def _batch_voice(voice_id: str | None) -> dict:
    """Resolve one queue-wide voice into concrete generation inputs.

    Clone profiles contribute their reference; designed profiles contribute
    their healed instruction and seed. Legacy ``preset:`` selections become
    the same instruction used by Dubbing instead of falling through to the
    engine default.
    """
    resolved = {
        "ref_audio": None,
        "ref_text": None,
        "instruct": "",
        "seed": None,
        "requires_cloning": False,
    }
    if not voice_id:
        return resolved
    if voice_id.startswith("preset:"):
        preset_id = voice_id.removeprefix("preset:")
        instruct = _BATCH_PRESET_INSTRUCT.get(preset_id)
        if instruct is None:
            raise ValueError("That built-in voice preset no longer exists")
        from omnivoice.utils.voice_design import sanitize_instruct

        resolved["instruct"] = sanitize_instruct(instruct)
        return resolved

    from core.config import VOICES_DIR
    from core.db import db_conn

    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?",
            (voice_id,),
        ).fetchone()
    if row is None:
        raise ValueError("That saved voice no longer exists")

    if row["kind"] == "design":
        from omnivoice.utils.voice_design import heal_design_instruct

        resolved["instruct"] = heal_design_instruct(row["instruct"], row["vd_states"])
        resolved["seed"] = int(row["seed"]) if row["seed"] is not None else None
        return resolved

    relative = row["locked_audio_path"] if row["is_locked"] else row["ref_audio_path"]
    if not relative:
        raise ValueError("That saved voice has no reference audio")
    ref_audio = os.path.join(VOICES_DIR, relative)
    if not os.path.isfile(ref_audio):
        raise ValueError("That saved voice's reference audio is missing")
    resolved.update({
        "ref_audio": ref_audio,
        "ref_text": row["ref_text"],
        "requires_cloning": True,
    })
    return resolved




# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("/batch/enqueue")
async def enqueue_batch_job(
    video: UploadFile = File(...),
    langs: str = Form("es"),            # comma-separated lang codes
    voice_id: Optional[str] = Form(None),
    preserve_bg: bool = Form(True),
    translation_provider: Optional[str] = Form(None),
):
    """Enqueue a video for batch dubbing.

    The video is saved to disk and a job is added to the queue.
    Returns the job ID for status polling.
    """
    _ensure_queue()

    job_id = str(uuid.uuid4())[:12]
    lang_list = [l.strip() for l in langs.split(",") if l.strip()]
    if not lang_list:
        raise HTTPException(400, "At least one target language is required")

    # Validate the snapshot before persisting a potentially large upload.
    # Resolve it again in the worker so deleting or editing a queued profile
    # cannot silently fall back to the engine's default voice.
    try:
        await asyncio.to_thread(_batch_voice, voice_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from business.carrier import call
    status = await call('status', {})
    ready = {item['capabilityContract'] for item in status['effectiveSelections'] if item['state'] == 'ready'}
    if not {'audio.transcribe', 'audio.synthesize', 'voice.create', 'text.generate'}.issubset(ready):
        raise HTTPException(409, 'Configure transcription, speech, voices and translation in Nimi AI settings')
    provider = 'nimi'

    # Save the uploaded video
    batch_dir = os.path.join(DATA_DIR, "batch")
    os.makedirs(batch_dir, exist_ok=True)
    ext = os.path.splitext(video.filename or "video.mp4")[1] or ".mp4"
    video_path = os.path.join(batch_dir, f"{job_id}{ext}")

    await _save_upload(video, video_path)

    job = {
        "id": job_id,
        "status": "queued",
        "filename": video.filename or f"{job_id}{ext}",
        "video_path": video_path,
        "langs": lang_list,
        "voice_id": voice_id,
        "preserve_bg": preserve_bg,
        "translation_provider": provider,
        "created_at": time.time(),
        "attempts": 1,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "progress": None,
    }
    _jobs[job_id] = job
    await _queue.put(job_id)

    logger.info(
        "Batch job %s enqueued (%d target languages)",
        log_safe(job_id), len(lang_list),
    )
    return {"job_id": job_id, "status": "queued", "queue_position": _queue.qsize()}


@router.get("/batch/jobs")
def list_batch_jobs(status: Optional[str] = None, limit: int = 50):
    """List batch jobs, optionally filtered by status."""
    jobs = list(_jobs.values())
    if status:
        if status == "active":
            jobs = [j for j in jobs if j["status"] in ("queued", "running")]
        elif status == "retryable":
            jobs = [j for j in jobs if j["status"] in ("failed", "cancelled")]
        else:
            jobs = [j for j in jobs if j["status"] == status]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs[:limit]


@router.get("/batch/jobs/{job_id}")
def get_batch_job(job_id: str):
    """Get the status of a specific batch job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/batch/jobs/{job_id}/cancel")
def cancel_batch_job(job_id: str):
    """Cancel a queued or running batch job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in ("done", "failed", "cancelled"):
        return {"already": job["status"]}
    was_running = job["status"] == "running" or job_id in _processing_job_ids
    job["status"] = "cancelled"
    if runner := _running_tasks.get(job_id):
        runner.cancel()
    job["retry_ready"] = not was_running
    job["finished_at"] = time.time()
    return {"cancelled": True}


@router.post("/batch/jobs/{job_id}/retry")
async def retry_batch_job(job_id: str):
    """Retry a terminal job using its original app-owned upload and settings."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, f"Job is {job['status']}, not retryable")
    if job_id in _processing_job_ids or not job.get("retry_ready", True):
        raise HTTPException(409, "The cancelled job is still stopping")
    if not os.path.isfile(job.get("video_path") or ""):
        raise HTTPException(409, "The original batch input is no longer available")

    try:
        await asyncio.to_thread(_batch_voice, job.get("voice_id"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from business.carrier import call
    status = await call('status', {})
    ready = {item['capabilityContract'] for item in status['effectiveSelections'] if item['state'] == 'ready'}
    if not {'audio.transcribe', 'audio.synthesize', 'voice.create', 'text.generate'}.issubset(ready):
        raise HTTPException(409, 'Configure the required capabilities in Nimi AI settings')

    for key in (
        "duration",
        "segments",
        "source_lang",
        "outputs",
        "warnings",
        "setup_required",
        "retry_ready",
    ):
        job.pop(key, None)
    job.update({
        "status": "queued",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "progress": None,
        "attempts": int(job.get("attempts", 1)) + 1,
    })
    _ensure_queue()
    await _queue.put(job_id)
    return {"job_id": job_id, "status": "queued", "queue_position": _queue.qsize()}


@router.delete("/batch/jobs/{job_id}")
def delete_batch_job(job_id: str):
    """Delete a batch job record and every app-owned input/output file."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("video_path"):
        try:
            unlink_if_present(job["video_path"])
        except FileCleanupError as exc:
            raise HTTPException(
                status_code=500,
                detail="Could not delete the batch video file. Close any app using it and retry.",
            ) from exc
    batch_root = os.path.realpath(os.path.join(DATA_DIR, "batch"))
    output_dir = os.path.realpath(os.path.join(batch_root, job_id))
    if os.path.dirname(output_dir) != batch_root:
        raise HTTPException(status_code=400, detail="Invalid batch job path")
    try:
        if os.path.isdir(output_dir):
            shutil.rmtree(output_dir)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not delete the batch output files. Close any app using them and retry.",
        ) from exc
    _jobs.pop(job_id, None)
    return {"deleted": True}


@router.get("/batch/download/{job_id}/{lang}")
def download_batch_output(job_id: str, lang: str):
    """Download a completed batch job's output video for a given language."""
    from fastapi.responses import FileResponse

    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "done":
        raise HTTPException(400, f"Job is {job['status']}, not done")

    outputs = job.get("outputs", {})
    path = outputs.get(lang)
    if not path or not os.path.exists(path):
        raise HTTPException(404, f"No output for language '{lang}'")

    filename = f"{os.path.splitext(job['filename'])[0]}_{lang}.mp4"
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
    )
