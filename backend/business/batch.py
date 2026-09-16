"""Batch orchestration reuses the interactive Nimi dubbing operations."""
import asyncio
import json
from pathlib import Path
import shutil

from business.dubbing import transcribe_job, translate, render_dub
from core.config import DUB_DIR
from schemas.requests import TranslateRequest, TranslateSegment, DubRequest, DubSegment
from services.dub_pipeline import ingest_pipeline, get_job
from services.ffmpeg_utils import find_ffmpeg, run_ffmpeg, bed_mix_filter


def resolve_batch_voice(voice_id):
    from api.routers.batch import _batch_voice
    from api.routers.audiobook import _resolve_voice
    if not voice_id:
        return None
    validated = _batch_voice(voice_id)
    return validated if voice_id.startswith('preset:') else _resolve_voice(voice_id)


async def run_batch(job_id, job):
    directory = Path(DUB_DIR) / job_id
    directory.mkdir(parents=True, exist_ok=True)
    resolve_batch_voice(job.get('voice_id'))
    source = Path(job['video_path'])
    target = directory / ('original' + source.suffix)
    await asyncio.to_thread(shutil.copyfile, source, target)
    total = 2 + 3 * len(job['langs'])
    completed = 0
    def progress(stage, **extra):
        job['progress'] = {'stage': stage, 'percent': round(completed / total * 100), **extra}
    progress('extract')
    async for update in ingest_pipeline(job_id, str(directory), {'kind': 'file', 'path': str(target), 'input_type': 'video'}, job['filename']):
        data = update.decode('utf-8') if isinstance(update, bytes) else update
        if data.startswith('data: '):
            event = json.loads(data[6:])
            if event.get('type') == 'error':
                raise RuntimeError(event.get('reason') or event.get('detail') or 'Media preparation failed')
    if job.get('preserve_bg') and not get_job(job_id).get('no_vocals_path'):
        raise ValueError('Background preservation requires successful Nimi source separation')
    completed += 1
    progress('transcribe')
    transcript = await transcribe_job(job_id)
    completed += 1
    outputs = {}
    for language in job['langs']:
        progress('translate', current_lang=language)
        translated = await translate(TranslateRequest(target_lang=language, source_lang=transcript['source_lang'],
            segments=[TranslateSegment(id=row['id'], text=row['text'], start=row['start'], end=row['end']) for row in transcript['segments']]))
        texts = {row['id']: row['text'] for row in translated['translated']}
        completed += 1
        progress('generate', current_lang=language)
        request = DubRequest(segments=[DubSegment(start=row['start'], end=row['end'], text=texts[row['id']],
            profile_id=job.get('voice_id') or '') for row in transcript['segments']],
            segment_ids=[row['id'] for row in transcript['segments']], language=language, language_code=language, timing_strategy='strict_slot')
        async for event in render_dub(job_id, request):
            data = json.loads(event[6:])
            if data['type'] == 'progress':
                progress('generate', current_lang=language, current_segment=data['current'], total_segments=data['total'])
        completed += 1
        progress('mix', current_lang=language)
        project = get_job(job_id)
        track = project['dubbed_tracks'][language]['path']
        output = directory / ('output_' + language + '.mp4')
        args = [find_ffmpeg(), '-y', '-i', str(target), '-i', track]
        if job.get('preserve_bg') and project.get('no_vocals_path'):
            args += ['-i', project['no_vocals_path'], '-filter_complex', bed_mix_filter('2:a', '1:a', out='mixed'), '-map', '0:v', '-map', '[mixed]']
        else:
            args += ['-map', '0:v', '-map', '1:a']
        args += ['-c:v', 'copy', '-c:a', 'aac', '-metadata', 'comment=VoiceStudio synthetic audio; neural watermark not applied', str(output)]
        code, _, error = await run_ffmpeg(args, job_id=job_id)
        if code:
            raise RuntimeError(error.decode('utf-8', 'replace')[-2000:])
        outputs[language] = str(output)
        completed += 1
    job['outputs'] = outputs
    progress('done')
