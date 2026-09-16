import asyncio
import base64
import io
import os
from pathlib import Path
import tempfile
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
import numpy as np
import soundfile as sf

from business.carrier import call, until_disconnected
from business.audio import transcribe_file
from services.aec import NlmsEchoCanceller

router = APIRouter()


async def transcribe_pcm(pcm: bytes, rate: int = 16000):
    buffer = io.BytesIO()
    samples = np.frombuffer(pcm, dtype="<i2")
    sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
    return await call("transcribe", {"base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "mimeType": "audio/wav", "language": "", "timestamps": False})


async def refine_text(text: str):
    from services.refinement import build_refinement_prompt, RefinementFlags, get_refinement_config, _note_refine_status
    configuration = get_refinement_config()
    if not text.strip() or not configuration.get('auto', True):
        return {}
    status = await call('status', {})
    if not any(item['capabilityContract'] == 'text.generate' and item['state'] == 'ready' for item in status['effectiveSelections']):
        return {'refinement_status': 'not-configured'}
    try:
        result = await call('translate', {'text': text, 'instruction': build_refinement_prompt(RefinementFlags.from_dict(configuration))})
        if not result['text'].strip():
            raise ValueError('Transcript refinement returned empty text')
        _note_refine_status(ok=True)
        return {'refined_text': result['text'], 'refinement_status': 'completed'}
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _note_refine_status(ok=False, reason=str(error))
        return {'refinement_status': 'failed', 'refinement_error': str(error)}


@router.get("/dictation/readiness")
async def readiness():
    status = await call("status", {})
    ready = any(item["capabilityContract"] == "audio.transcribe" and item["state"] == "ready"
                for item in status["effectiveSelections"])
    return {"ready": ready}


@router.get('/dictation/prefs')
def dictation_preferences():
    from core.prefs import get
    return {'enabled': get('dictation.enabled', False), 'mode': get('dictation.mode', 'hold')}


@router.post('/dictation/prefs')
@router.put('/dictation/prefs')
async def update_dictation_preferences(request: Request):
    from core.prefs import set_
    body = await request.json()
    if 'enabled' in body:
        if not isinstance(body['enabled'], bool):
            raise ValueError('Invalid dictation preference')
        set_('dictation.enabled', body['enabled'])
    if 'mode' in body:
        if body['mode'] not in {'hold', 'toggle'}:
            raise ValueError('Invalid dictation shortcut mode')
        set_('dictation.mode', body['mode'])
    return dictation_preferences()


@router.post("/transcribe")
async def transcribe(request: Request):
    form = await request.form()
    upload = form.get("audio")
    if not upload:
        raise ValueError("Audio is required")
    started = time.monotonic()
    suffix = Path(upload.filename or "audio.wav").suffix
    with tempfile.TemporaryDirectory(prefix="voicestudio-transcribe-") as directory:
        source = Path(directory) / ("input" + suffix)
        source.write_bytes(await upload.read())
        from business.media import decode_to_wav
        decoded = Path(directory) / 'decoded.wav'
        await decode_to_wav(source, decoded)
        source = decoded
        samples, rate = sf.read(source, dtype="float32", always_2d=True)
        texts = []
        languages = set()
        for offset in range(0, len(samples), 240 * rate):
            if await request.is_disconnected():
                raise asyncio.CancelledError()
            window = Path(directory) / "window.wav"
            sf.write(window, samples[offset:offset + 240 * rate], rate)
            operation = asyncio.create_task(transcribe_file(window))
            try:
                while not operation.done():
                    if await request.is_disconnected():
                        operation.cancel()
                        raise asyncio.CancelledError()
                    await asyncio.wait({operation}, timeout=0.2)
                result = await operation
            finally:
                if not operation.done():
                    operation.cancel()
            if result["text"]:
                texts.append(result["text"])
            if result['language']:
                languages.add(result['language'])
        text = ' '.join(texts)
        refinement = await until_disconnected(request, refine_text(text)) if form.get('refine') == 'true' else {}
        return {"text": text, **refinement, "segments": [], "language": next(iter(languages)) if len(languages) == 1 else '',
                'status': 'transcribed' if text else 'no-speech',
                "duration_s": len(samples) / rate, "transcription_time_s": time.monotonic() - started,
                "engine": "Nimi", "mode": form.get("mode", "accurate")}


@router.websocket("/ws/transcribe")
async def live(ws: WebSocket):
    import hmac
    if not hmac.compare_digest(ws.query_params.get("worker_token", ""), os.environ["VOICESTUDIO_WORKER_SECRET"]):
        await ws.close(code=1008)
        return
    await ws.accept()
    buffer = bytearray()
    ended = asyncio.Event()
    changed = asyncio.Event()
    committed = []
    total_samples = 0
    quiet_samples = 0
    epoch = 0
    canceller = NlmsEchoCanceller(sample_rate=16000) if ws.query_params.get("aec") == "1" else None

    async def receive():
        nonlocal total_samples, quiet_samples
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect()
            if message.get("text") == "EOF":
                ended.set()
                changed.set()
                return
            if data := message.get("bytes"):
                if canceller:
                    kind, data = data[0], data[1:]
                    if kind == 1:
                        canceller.push_far_end(data)
                        continue
                    if kind != 0:
                        raise ValueError("Unknown audio frame")
                    data = canceller.process_near_end(data)
                if len(data) % 2:
                    raise ValueError("PCM frame is not 16-bit aligned")
                samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
                total_samples += len(samples)
                rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0
                quiet_samples = quiet_samples + len(samples) if rms < 0.01 else 0
                buffer.extend(data)
                # Bounded audio queue; never silently drop frames under backpressure.
                if len(buffer) > 16000 * 2 * 120:
                    raise ValueError("Transcription cannot keep up with this recording")
                changed.set()

    async def recognize():
        nonlocal quiet_samples, epoch
        last_partial_size = 0
        last_partial = 0.0
        while True:
            await changed.wait()
            changed.clear()
            seconds = len(buffer) / 32000
            commit = ended.is_set() or (quiet_samples >= 9600 and seconds >= 0.3) or seconds >= 15
            if commit and buffer:
                pcm = bytes(buffer)
                buffer.clear()
                quiet_samples = 0
                epoch += 1
                last_partial_size = 0
                started = time.monotonic()
                result = await transcribe_pcm(pcm)
                if result["text"]:
                    committed.append(result["text"])
                    refinement = await refine_text(result['text'])
                    await ws.send_json({"type": "final", "final_kind": "utterance", "text": result["text"],
                        **refinement,
                        "segments": [], "language": result["language"], "engine": "Nimi",
                        "duration_s": len(pcm) / 32000, "transcription_time_s": time.monotonic() - started})
            elif seconds >= 1 and len(buffer) != last_partial_size and time.monotonic() - last_partial >= 2:
                snapshot = bytes(buffer)
                last_partial_size = len(snapshot)
                partial_epoch = epoch
                result = await transcribe_pcm(snapshot)
                last_partial = time.monotonic()
                if epoch == partial_epoch:
                    await ws.send_json({"type": "partial", "text": result["text"]})
            if ended.is_set() and not buffer:
                await ws.send_json({"type": "final", "final_kind": "summary", "text": " ".join(committed),
                                    "segments": [], "duration_s": total_samples / 16000, "engine": "Nimi"})
                return

    receiver = asyncio.create_task(receive())
    recognizer = asyncio.create_task(recognize())
    try:
        done, _ = await asyncio.wait({receiver, recognizer}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if receiver in done:
            await recognizer
    except WebSocketDisconnect:
        pass
    except Exception as error:
        await ws.send_json({"type": "error", "message": str(error)})
    finally:
        receiver.cancel()
        recognizer.cancel()
        await asyncio.gather(receiver, recognizer, return_exceptions=True)
