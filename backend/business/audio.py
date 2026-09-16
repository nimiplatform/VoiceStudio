import asyncio
import base64
import io
import json
import re
from pathlib import Path
import struct
import time
import tempfile
import uuid

import numpy as np
import soundfile as sf

from core.config import OUTPUTS_DIR, VOICES_DIR
from core.db import db_conn
from core import event_bus
from business.carrier import call
from omnivoice.utils.lang_map import LANG_NAME_TO_ID


def mark_synthetic(wav: bytes, *, context: str) -> bytes:
    """Non-neural, machine-readable WAV provenance. Does not claim AudioSeal."""
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("Synthetic output must be WAV")
    note = json.dumps({"synthetic": True, "application": "VoiceStudio", "context": context,
                       "neuralWatermark": "not-applied"}, separators=(",", ":")).encode("utf-8") + b"\0"
    comment = b"ICMT" + struct.pack("<I", len(note)) + note + (b"\0" if len(note) % 2 else b"")
    payload = b"INFO" + comment
    chunk = b"LIST" + struct.pack("<I", len(payload)) + payload
    result = bytearray(wav + chunk)
    struct.pack_into("<I", result, 4, len(result) - 8)
    return bytes(result)


async def transcribe_file(path, *, language="", timestamps=False):
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    duration = len(audio) / rate
    if duration > 300:
        raise ValueError("Split transcription audio into windows of at most 300 seconds")
    encoded = io.BytesIO()
    sf.write(encoded, audio, rate, format="WAV", subtype="PCM_16")
    return await call("transcribe", {"base64": base64.b64encode(encoded.getvalue()).decode("ascii"),
                                     "mimeType": "audio/wav", "language": language, "timestamps": timestamps})


async def render_sample(settings, destination: Path):
    text = settings.get("sample_script") or "This is a preview of my voice."
    audio, _ = await synthesize(text=text, language=settings.get("language", ""),
                                instruction=settings.get("instruct", ""))
    destination.write_bytes(audio)


async def synthesize(*, text, language="", speed=1.0, instruction="", reference_path=None, reference_text="", postprocess=True, lexicon=None):
    if re.search(r"\[(?:laughter|sigh|confirmation-en|question-(?:en|ah|oh|ei|yi)|surprise-(?:ah|oh|wa|yo)|dissatisfaction-hnn)\]", text, re.I):
        raise ValueError('NIMI_EXPRESSION_TAGS')
    code = "" if language == "Auto" else LANG_NAME_TO_ID.get(language.lower(), language)
    from services.pronunciation import apply_lexicon, load_dict_for_request
    pronunciations = {**load_dict_for_request(code), **(lexicon or {})}
    text = apply_lexicon(text, pronunciations)
    payload = {"text": text, "language": code, "instruction": instruction}
    if reference_path:
        source, rate = sf.read(reference_path, dtype="float32", always_2d=True)
        buffer = io.BytesIO()
        sf.write(buffer, source, rate, format="WAV", subtype="PCM_16")
        if not reference_text.strip():
            result = await transcribe_file(reference_path, language=payload["language"])
            reference_text = result["text"]
            if not reference_text.strip():
                raise ValueError("Reference recording contains no recognized speech")
        payload["reference"] = {"base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
                                "mimeType": "audio/wav", "text": reference_text}
    result = await call("synthesize", payload)
    return await prepare_speech_output(result, speed=speed, postprocess=postprocess)


async def prepare_speech_output(result, *, speed=1.0, postprocess=True):
    raw = base64.b64decode(result["base64"], validate=True)
    if not 0.5 <= speed <= 2:
        raise ValueError('Speech speed must be between 0.5 and 2')
    if speed != 1:
        from business.media import decode_to_wav
        from services.ffmpeg_utils import _atempo_chain
        with tempfile.TemporaryDirectory(prefix='voicestudio-speed-') as directory:
            source = Path(directory) / 'source.wav'
            target = Path(directory) / 'speed.wav'
            source.write_bytes(raw)
            await decode_to_wav(source, target, filters=_atempo_chain(speed))
            raw = target.read_bytes()
    samples, rate = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    if not len(samples) or not np.isfinite(samples).all():
        raise ValueError("Synthesis returned empty or invalid audio")
    if postprocess:
        from pedalboard import Pedalboard, HighpassFilter, Compressor
        board = Pedalboard([HighpassFilter(cutoff_frequency_hz=60),
            Compressor(threshold_db=-15, ratio=1.5, attack_ms=2, release_ms=100)])
        samples = board(samples.T, rate).T
        peak = float(np.max(np.abs(samples)))
        if peak > 10 ** (-50 / 20):
            samples = samples * (10 ** (-2 / 20) / peak)
    output = io.BytesIO()
    sf.write(output, samples, rate, format="WAV", subtype="PCM_16")
    return mark_synthetic(output.getvalue(), context="synthesis"), {"sample_rate": rate,
        "channels": samples.shape[1], "duration": len(samples) / rate, "samples": samples}


async def generate_take(form):
    text = str(form.get("text", "")).strip()
    if not text:
        raise ValueError("Speech text is required")
    reference_path = None
    reference_text = str(form.get("ref_text", ""))
    instruction = str(form.get("instruct", ""))
    profile_id = form.get("profile_id")
    temporary = None
    if profile_id:
        with db_conn() as conn:
            profile = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
        if not profile:
            raise ValueError("Voice profile no longer exists")
        filename = profile["locked_audio_path"] or profile["ref_audio_path"]
        if filename:
            reference_path = str(Path(VOICES_DIR) / Path(filename).name)
        reference_text = profile["ref_text"] or reference_text
        instruction = profile["instruct"] or instruction
    elif upload := form.get("ref_audio"):
        temporary = Path(VOICES_DIR) / ("request-" + uuid.uuid4().hex + ".wav")
        temporary.write_bytes(await upload.read())
        reference_path = str(temporary)
    started = time.monotonic()
    cleaned_reference = None
    try:
        if reference_path and str(form.get('denoise', 'false')).lower() == 'true':
            from business.media import decode_to_wav
            cleaned_reference = Path(VOICES_DIR) / ('clean-' + uuid.uuid4().hex + '.wav')
            await decode_to_wav(reference_path, cleaned_reference, filters='highpass=f=60,afftdn=nf=-30')
            reference_path = str(cleaned_reference)
        audio, info = await synthesize(text=text, language=str(form.get("language", "")),
            speed=float(form.get("speed", 1)), instruction=instruction,
            reference_path=reference_path, reference_text=reference_text,
            postprocess=str(form.get('postprocess_output', 'true')).lower() == 'true')
        task = asyncio.current_task()
        if task and task.cancelling():
            raise asyncio.CancelledError()
        identifier = uuid.uuid4().hex[:12]
        filename = identifier + ".wav"
        destination = Path(OUTPUTS_DIR) / filename
        destination.write_bytes(audio)
        elapsed = time.monotonic() - started
        try:
            with db_conn() as conn:
                conn.execute("INSERT INTO generation_history (id,text,mode,language,instruct,profile_id,audio_path,duration_seconds,generation_time,seed,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, text, "clone" if reference_path else "design", form.get("language", "Auto"),
                     instruction, profile_id, filename, info["duration"], elapsed, None, time.time()))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        event_bus.emit("generation_history", {"action": "created", "id": identifier})
        return audio, {**info, "id": identifier, "audio_path": filename, "gen_time": elapsed}
    finally:
        if cleaned_reference:
            cleaned_reference.unlink(missing_ok=True)
        if temporary:
            temporary.unlink(missing_ok=True)
