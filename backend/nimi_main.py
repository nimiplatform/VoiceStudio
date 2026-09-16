"""VoiceStudio's local business API. Nimi inference runs only in the parent Host."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hmac
import io
import json
import os
from pathlib import Path
import platform
import sys

# Make the App's deterministic utility package available in source and frozen builds.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Retain the App's existing data-location preference without loading old model
# credentials or inference settings into this business-only process.
if not os.environ.get('OMNIVOICE_DATA_DIR'):
    from core.user_env import get_user_env
    saved_data_dir = get_user_env('OMNIVOICE_DATA_DIR')
    if saved_data_dir:
        os.environ['OMNIVOICE_DATA_DIR'] = os.path.expandvars(os.path.expanduser(saved_data_dir.strip('"\'')))

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from business import carrier
from business.carrier import until_disconnected
from business.audio import generate_take
from core.config import DATA_DIR, OUTPUTS_DIR, VOICES_DIR
from core.db import init_db
from core.version import APP_VERSION

_secret = os.environ.get("VOICESTUDIO_WORKER_SECRET", "")


@asynccontextmanager
async def lifespan(app):
    if not _secret:
        raise RuntimeError("The VoiceStudio business worker must be launched by its Host")
    await asyncio.to_thread(init_db)
    from core.tasks import task_manager
    from core import job_store
    job_store.sweep_orphans_on_startup()
    worker = asyncio.create_task(task_manager.worker())
    try:
        yield
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title="VoiceStudio business", version=APP_VERSION, lifespan=lifespan)


class WorkerAuthentication:
    """Authenticate without buffering ASGI receive or hiding http.disconnect."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            from starlette.datastructures import Headers
            token = Headers(scope=scope).get('X-VoiceStudio-Worker', '')
            if not _secret or not hmac.compare_digest(token, _secret):
                response = JSONResponse({'detail': 'Business worker access denied'}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(WorkerAuthentication)

@app.exception_handler(ValueError)
async def bad_input(request, error):
    return JSONResponse({"detail": str(error)}, status_code=422)


@app.exception_handler(RuntimeError)
async def operation_failed(request, error):
    return JSONResponse({"detail": str(error)}, status_code=502)


@app.get("/health")
def health():
    return {"service": "voicestudio-business", "status": "ok", "version": APP_VERSION}


@app.get("/system/info")
def system_info():
    return {"app_version": APP_VERSION, "python": platform.python_version(), "platform": sys.platform,
            "arch": platform.machine(), "device": "Nimi", "data_dir": DATA_DIR,
            "outputs_dir": OUTPUTS_DIR, "model_checkpoint": None, "asr_model": None}


@app.get("/engines")
async def engines():
    snapshot = await carrier.call("status", {})
    choices = {item["capabilityContract"]: item for item in snapshot["effectiveSelections"]}
    result = {}
    for kind, contract in (("tts", "audio.synthesize"), ("asr", "audio.transcribe"), ("llm", "text.generate")):
        choice = choices.get(contract)
        ready = bool(choice and choice["state"] == "ready")
        result[kind] = {"active": "nimi", "backends": [{"id": "nimi", "display_name": "Nimi",
            "available": ready, "reason": None if ready else "Configure this capability in Nimi AI settings",
            "one_click_install": False}]}
    return result


@app.post("/generate")
async def generate(request: Request):
    form = await request.form()
    if form.get("stream") == "true":
        async def stream():
            try:
                wav, info = await until_disconnected(request, generate_take(form))
                # This is a completed model result, emitted as one real preview chunk.
                yield json.dumps({"type": "start", "sample_rate": info["sample_rate"], "total_chunks": 1,
                                  "synthetic": True, "neuralWatermark": "not-applied"}) + "\n"
                if info["channels"] == 1:
                    pcm = (info["samples"].clip(-1, 1) * 32767).astype("<i2").tobytes()
                    yield json.dumps({"type": "chunk", "pcm": base64.b64encode(pcm).decode("ascii")}) + "\n"
                yield json.dumps({"type": "done", **{key: value for key, value in info.items() if key != "samples"}}) + "\n"
            except asyncio.CancelledError:
                raise
            except Exception as error:
                yield json.dumps({"type": "error", "detail": str(error), "retryable": False}) + "\n"
        return StreamingResponse(stream(), media_type="application/x-ndjson")
    wav, info = await until_disconnected(request, generate_take(form))
    return Response(wav, media_type="audio/wav", headers={
        "X-Audio-Id": info["id"], "X-Audio-Path": info["audio_path"],
        "X-Audio-Duration": str(info["duration"]), "X-Gen-Time": str(info["gen_time"]),
    })


@app.post('/generate/recover')
async def recover_completed_generation(request: Request):
    """Receive an owned completed speech job without running inference again."""
    import time
    import uuid
    from business.audio import prepare_speech_output
    from core.db import db_conn
    from core import event_bus
    body = await request.json()
    if not isinstance(body.get('text'), str) or not body['text'].strip():
        raise ValueError('The original speech text is required')
    result = await until_disconnected(request, carrier.call('recover-speech', {'jobId': body.get('job_id')}))
    wav, info = await prepare_speech_output(result, speed=float(body.get('speed', 1)), postprocess=bool(body.get('postprocess', True)))
    identifier = uuid.uuid4().hex[:12]
    filename = identifier + '.wav'
    file = Path(OUTPUTS_DIR) / filename
    file.write_bytes(wav)
    try:
        with db_conn() as conn:
            conn.execute('INSERT INTO generation_history (id,text,mode,language,profile_id,audio_path,duration_seconds,generation_time,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (identifier, body['text'], 'clone' if body.get('profile_id') else 'design', body.get('language') or 'Auto',
                 body.get('profile_id'), filename, info['duration'], None, time.time()))
    except Exception:
        file.unlink(missing_ok=True)
        raise
    event_bus.emit('generation_history', {'action': 'created', 'id': identifier})
    return {'id': identifier, 'audio_path': filename, 'duration': info['duration'], 'job_id': result['jobId']}


@app.get("/audio/{filename:path}")
def audio(filename: str):
    from core.path_security import resolve_within, UnsafePath
    try:
        file = resolve_within(OUTPUTS_DIR, filename)
    except UnsafePath as error:
        raise HTTPException(400, "Invalid audio name") from error
    if not file.is_file():
        raise HTTPException(404, "Audio not found")
    return FileResponse(file)


@app.websocket('/ws/events')
async def events(ws: WebSocket):
    if not hmac.compare_digest(ws.query_params.get('worker_token', ''), _secret):
        await ws.close(code=1008)
        return
    from api.routers.events import ws_events
    await ws_events(ws)


@app.get('/watermark/status')
def watermark_status():
    from core.prefs import get
    return {'invisible_enabled': get('watermark.invisible', True), 'audioseal_available': False,
            'visible_audio_enabled': get('watermark.visible_audio', False),
            'visible_video_enabled': get('watermark.visible_video', True),
            'provenance': 'file-metadata', 'neural_watermark': 'not-applied'}


@app.post('/watermark/settings')
def watermark_settings(invisible: bool | None = None, visible_audio: bool | None = None, visible_video: bool | None = None):
    from core.prefs import set_
    for name, value in [('invisible', invisible), ('visible_audio', visible_audio), ('visible_video', visible_video)]:
        if value is not None:
            set_('watermark.' + name, value)
    return watermark_status()


from api.routers import projects, profiles, exports, glossary, pronunciation, describe_voice, profile_images, audiobook, dub_core, dub_export, longform_jobs, gallery, archetypes, media_tools, batch
from business import history, transcription, dubbing, media, tools
for module in (projects, profiles, exports, glossary, pronunciation, describe_voice, profile_images, history, transcription, audiobook, dub_core, dub_export, longform_jobs, dubbing, gallery, archetypes, media, media_tools, batch, tools):
    app.include_router(module.router)

# Only App-owned preferences; old credentials and model installation are not exposed.
from api.routers import settings
for route in settings.router.routes:
    if route.path in {'/api/settings/analytics', '/api/settings/db-backup', '/api/settings/history-retention', '/api/settings/dictation-refinement', '/api/settings/storage', '/api/settings/storage/temp/clear'}:
        app.router.routes.append(route)

app.mount('/demo_audio', StaticFiles(directory=Path(__file__).parent / 'assets/samples'), name='demo-audio')


if __name__ == "__main__":
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(os.environ.get("OMNIVOICE_PORT", "3900")), log_config=None))
    carrier.start(lambda: setattr(server, "should_exit", True))
    server.run()
