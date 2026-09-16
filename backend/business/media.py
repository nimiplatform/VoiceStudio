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
    import time
    from business.audio import transcribe_file, synthesize, persist_take, mark_synthetic, voice_binding
    from api.routers.audiobook import _resolve_voice, _voice_profile_exists
    from api.routers.voice_convert import _clamped_tempo_ratio, _copy_source_upload
    from services.ffmpeg_utils import _atempo_chain
    from business.carrier import until_disconnected
    form = await request.form()
    uploaded, profile = form.get('audio'), form.get('profile_id')
    if not uploaded or not profile or not _voice_profile_exists(profile):
        raise ValueError('A source recording and existing saved voice are required')
    voice = _resolve_voice(profile)
    match = str(form.get('match_duration', 'true')).lower() in {'1', 'true'}
    async def render():
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='voicestudio-convert-') as directory:
            source, decoded = Path(directory) / 'input', Path(directory) / 'input.wav'
            with source.open('wb') as output:
                await _copy_source_upload(uploaded, output)
            await decode_to_wav(source, decoded)
            transcript = await transcribe_file(decoded)
            if not transcript['text'].strip():
                raise ValueError('Source contains no recognized speech')
            wav, info = await synthesize(text=transcript['text'], language=transcript['language'],
                reference_path=voice['ref_audio'], reference_text=voice['ref_text'] or '', instruction=voice['instruct'] or '', **voice_binding(voice))
            ratio = _clamped_tempo_ratio(info['duration'], sf.info(decoded).duration) if match else None
            if ratio is not None:
                generated, adjusted = Path(directory) / 'generated.wav', Path(directory) / 'matched.wav'
                generated.write_bytes(wav)
                await decode_to_wav(generated, adjusted, filters=_atempo_chain(ratio))
                samples, rate = sf.read(adjusted, dtype='float32', always_2d=True)
                wav = mark_synthetic(adjusted.read_bytes(), context='voice-conversion')
                info = {**info, 'duration': len(samples)/rate, 'samples': samples, 'sample_rate': rate, 'channels': samples.shape[1]}
            _, saved = persist_take(wav, info, text=transcript['text'], mode='clone' if voice['ref_audio'] else 'design',
                language=transcript['language'], instruction=voice['instruct'] or '', profile_id=profile, started=started)
            return {'id': saved['id'], 'audio_url': '/audio/' + saved['audio_path'], 'text': transcript['text'],
                    'duration_s': saved['duration'], 'gen_time_s': saved['gen_time']}
    return await until_disconnected(request, render())
