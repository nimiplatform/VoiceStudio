"""VoiceStudio text direction and timing tools over the protected Host."""
import json
import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from business.carrier import call, until_disconnected
from services.incremental import plan_incremental
from services.effect_presets import EFFECT_PRESETS
from services.ffmpeg_utils import find_ffprobe, spawn_subprocess

router = APIRouter()


@router.post('/tools/probe')
async def probe(request: Request):
    body = await request.json()
    source = Path(body['path']).expanduser().resolve()
    if not source.is_file():
        raise ValueError('Media file not found')
    ffprobe = find_ffprobe()
    if not ffprobe:
        raise RuntimeError('ffprobe is not installed')
    process = await spawn_subprocess(ffprobe, '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', str(source),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output, error = await process.communicate()
    if process.returncode:
        raise ValueError(error.decode('utf-8', 'replace'))
    return json.loads(output.decode('utf-8'))


@router.post('/tools/direction')
async def direction(request: Request):
    from services.director import TAXONOMY, Direction, _normalize
    body = await request.json()
    text = body.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('A direction is required')
    result = await until_disconnected(request, call('translate', {'text': text, 'json': True,
        'instruction': 'Map the delivery direction to this taxonomy. Return a JSON object whose keys are dimensions and values are arrays of allowed tokens. ' + json.dumps(TAXONOMY)}))
    tokens = json.loads(result['text'])
    if not isinstance(tokens, dict):
        raise ValueError('Direction analysis returned an invalid object')
    parsed = Direction(tokens=_normalize(tokens), source=text, method='nimi')
    return {'tokens': parsed.tokens, 'instruct_prompt': parsed.instruct_prompt(),
            'translate_hint': parsed.translate_hint(), 'rate_bias': parsed.rate_bias(),
            'method': 'nimi', 'error': None, 'taxonomy': TAXONOMY}


@router.post('/tools/rate-fit')
async def rate_fit(request: Request):
    from services.speech_rate_estimate import expected_duration, rate_ratio
    body = await request.json()
    text, language = body.get('text'), body.get('target_lang')
    seconds = float(body.get('slot_seconds', 0))
    if not isinstance(text, str) or not isinstance(language, str) or seconds <= 0:
        raise ValueError('Text, language and a positive time slot are required')
    result = await until_disconnected(request, call('translate', {'text': text,
        'instruction': f'Rewrite this spoken {language} line concisely for a {seconds:g}-second scene. Preserve all facts and names. Return only the spoken text.'}))
    revised = result['text'].strip()
    if not revised:
        raise ValueError('The timing tool returned empty text')
    return {'text': revised, 'changed': revised != text, 'rate_ratio': rate_ratio(revised, seconds, language),
            'estimated_duration_s': expected_duration(revised, language), 'measurement': 'text-length-estimate', 'method': 'nimi'}


@router.post('/tools/incremental')
async def incremental(request: Request):
    body = await request.json()
    return plan_incremental(body['segments'], stored_hashes=body.get('stored_hashes') or {},
                            track_lang=body.get('lang'), voice_match=body.get('voice_match'))


@router.get('/tools/effects')
def effects():
    return [{'id': key, **{name: value[name] for name in ['label', 'icon', 'description']}}
            for key, value in EFFECT_PRESETS.items()]
