import io
from pathlib import Path
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import Response
import soundfile as sf

from services.ffmpeg_utils import find_ffmpeg, run_ffmpeg

router = APIRouter()


async def decode_to_wav(source, destination, *, filters=None):
    command = [find_ffmpeg(), '-y', '-i', str(source), '-vn']
    if filters:
        command += ['-af', filters]
    command += ['-c:a', 'pcm_s16le', str(destination)]
    code, _, stderr = await run_ffmpeg(command)
    if code:
        raise ValueError(stderr.decode('utf-8', 'replace')[-2000:])


@router.post('/clean-audio')
async def clean_audio(request: Request):
    form = await request.form()
    file = form.get('audio')
    if not file:
        raise ValueError('Audio is required')
    with tempfile.TemporaryDirectory(prefix='voicestudio-clean-') as directory:
        source, output = Path(directory) / 'input', Path(directory) / 'clean.wav'
        source.write_bytes(await file.read())
        await decode_to_wav(source, output, filters='highpass=f=60,afftdn=nf=-30')
        return Response(output.read_bytes(), media_type='audio/wav', headers={'X-Clean-Filename': 'clean.wav'})


@router.post('/convert')
async def convert_voice(request: Request):
    from business.audio import transcribe_file, generate_take, mark_synthetic
    from core.config import OUTPUTS_DIR
    from core.db import db_conn
    from services.ffmpeg_utils import _atempo_chain
    from business.carrier import until_disconnected
    form = await request.form()
    uploaded = form.get('audio')
    profile = form.get('profile_id')
    if not uploaded or not profile:
        raise ValueError('A source recording and saved voice are required')
    with tempfile.TemporaryDirectory(prefix='voicestudio-convert-') as directory:
        source, decoded = Path(directory) / 'input', Path(directory) / 'input.wav'
        source.write_bytes(await uploaded.read())
        await decode_to_wav(source, decoded)
        transcript = await until_disconnected(request, transcribe_file(decoded))
        if not transcript['text'].strip():
            raise ValueError('Source contains no recognized speech')
        wav, info = await until_disconnected(request, generate_take({'text': transcript['text'], 'profile_id': profile,
            'language': transcript['language'], 'speed': 1}))
        destination = Path(OUTPUTS_DIR) / info['audio_path']
        if form.get('match_duration') == '1':
            duration = sf.info(decoded).duration
            adjusted = Path(directory) / 'matched.wav'
            await decode_to_wav(destination, adjusted, filters=_atempo_chain(info['duration'] / duration))
            pending = destination.with_suffix('.part')
            pending.write_bytes(mark_synthetic(adjusted.read_bytes(), context='voice-conversion'))
            pending.replace(destination)
            info['duration'] = sf.info(destination).duration
            with db_conn() as conn:
                conn.execute('UPDATE generation_history SET duration_seconds=? WHERE id=?', (info['duration'], info['id']))
        return {'id': info['id'], 'audio_url': '/audio/' + info['audio_path'], 'text': transcript['text'],
                'duration_s': info['duration'], 'gen_time_s': info['gen_time']}
