"""Nimi synthesis inside VoiceStudio's existing chapter planning/resume/export flow."""
import asyncio
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from business.audio import synthesize, mark_synthetic, voice_binding
from business.carrier import call
from services.text_normalization import normalize_for_tts
from services.chunked_tts import split_text_into_chunks
from services.pronunciation import load_dict_for_request


async def render_chapter(chapter, *, default_voice, language, opts, voice_map, lexicon, cache_dir):
    from api.routers.audiobook import _resolve_voice, _map_span_voice
    voices = {}
    fingerprints = {}
    for span in chapter.spans:
        key = _map_span_voice(span.voice_id, default_voice, voice_map)
        if key not in voices:
            voice = _resolve_voice(key)
            voices[key] = voice
            fingerprints[key or ""] = {**voice, "reference_sha256":
                hashlib.sha256(Path(voice["ref_audio"]).read_bytes()).hexdigest() if voice["ref_audio"] else None}
    status = await call("status", {})
    signature = {"chapter": asdict(chapter), "voices": fingerprints, "voice_map": voice_map,
                 "language": language, "lexicon": lexicon, "pronunciation": load_dict_for_request(language), "options": opts.to_manifest(),
                 "configuration": status["fingerprint"], "provenance": "nimi-wave-metadata-v1"}
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest()
    destination = Path(cache_dir) / (key + ".wav")
    if destination.is_file() and not opts.vary_repeats:
        info = sf.info(destination)
        return str(destination), info.duration, True, None
    chunks = []
    sample_rate = None
    for span in chapter.spans:
        text = normalize_for_tts(span.text, language)
        if text.strip():
            voice = voices[_map_span_voice(span.voice_id, default_voice, voice_map)]
            for chunk in split_text_into_chunks(text):
                wav, info = await synthesize(text=chunk, language=language or "", speed=span.speed or 1.0,
                    reference_path=voice["ref_audio"], reference_text=voice["ref_text"] or "",
                    **voice_binding(voice),
                    instruction=voice["instruct"] or "", lexicon=lexicon,
                    postprocess=opts.postprocess_output is not False)
                sample_rate = sample_rate or info["sample_rate"]
                samples = info["samples"]
                if info["sample_rate"] != sample_rate:
                    samples = soxr.resample(samples, info["sample_rate"], sample_rate)
                chunks.append(samples)
        if span.pause_ms_after:
            if sample_rate is None:
                # Hold a leading pause until the first real artifact defines its rate.
                chunks.append(int(span.pause_ms_after))
            else:
                chunks.append(np.zeros((round(span.pause_ms_after * sample_rate / 1000), 1), dtype=np.float32))
    if sample_rate is None:
        raise ValueError("Chapter has no speech")
    arrays = [np.zeros((round(item * sample_rate / 1000), 1), dtype=np.float32) if isinstance(item, int) else item for item in chunks]
    channels = max(item.shape[1] for item in arrays)
    arrays = [np.repeat(item, channels, axis=1) if item.shape[1] == 1 else item for item in arrays]
    audio = np.concatenate(arrays, axis=0)
    output = io.BytesIO()
    sf.write(output, audio, sample_rate, format="WAV", subtype="PCM_16")
    marked = mark_synthetic(output.getvalue(), context="audiobook.chapter")
    if asyncio.current_task().cancelling():
        raise asyncio.CancelledError()
    temporary = destination.with_suffix('.part')
    temporary.write_bytes(marked)
    temporary.replace(destination)
    return str(destination), len(audio) / sample_rate, False, None
