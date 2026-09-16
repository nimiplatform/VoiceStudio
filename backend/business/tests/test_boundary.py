import asyncio
import io
import os
from pathlib import Path
import sys
import tempfile
import wave

import numpy as np
import soundfile as sf

_data = tempfile.TemporaryDirectory(prefix='voicestudio-business-test-')
os.environ['OMNIVOICE_DATA_DIR'] = _data.name
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_CACHE'] = str(Path(_data.name) / 'empty-hf')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from business.audio import mark_synthetic
from business.dubbing import _segments
from core.db import init_db
from core import job_store
from core.tasks import TaskManager


def test_public_reference_url_rejects_local_destinations_and_credentials():
    import pytest
    from business.reference import public_audio_url
    for value in ['file:///tmp/a.wav', 'http://127.0.0.1/a', 'http://[::1]/a',
                  'http://localhost/a', 'https://user:password@example.com/a', 'https://example.com/a#fragment']:
        with pytest.raises(ValueError):
            public_audio_url(value)
    assert public_audio_url('https://example.com/voice.wav') == 'https://example.com/voice.wav'


def test_reference_url_blocks_redirect_to_private_address(monkeypatch):
    import httpx
    import pytest
    from business import reference
    async def scenario():
        loop = asyncio.get_running_loop()
        async def addresses(host, port, **kwargs):
            return [(2, 1, 6, '', ('93.184.215.14', port))]
        monkeypatch.setattr(loop, 'getaddrinfo', addresses)
        requests = []
        async def transport(request):
            requests.append(request)
            return httpx.Response(302, headers={'location':'http://127.0.0.1/private'})
        client_type = httpx.AsyncClient
        monkeypatch.setattr(reference.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(transport), **kwargs))
        with pytest.raises(ValueError, match='public'):
            await reference.download_reference('https://example.com/voice.wav')
        assert len(requests) == 1
        assert requests[0].url.host == '93.184.215.14'
        assert requests[0].headers['host'] == 'example.com'
        assert requests[0].extensions['sni_hostname'] == 'example.com'
    asyncio.run(scenario())


def test_saved_voice_asset_and_uri_survive_lock_unlock_and_db_reopen(monkeypatch):
    from fastapi.testclient import TestClient
    import nimi_main
    from core.db import db_conn
    from core.config import OUTPUTS_DIR
    from api.routers.audiobook import _resolve_voice
    monkeypatch.setattr(nimi_main, '_secret', 'business-unit-test')
    wav = io.BytesIO(); sf.write(wav, np.zeros(2400), 24000, format='WAV', subtype='PCM_16')
    with TestClient(nimi_main.app, headers={'X-VoiceStudio-Worker':'business-unit-test'}) as client:
        created = client.post('/profiles', data={'name':'URL voice','ref_audio_uri':'https://example.com/reference.wav',
            'ref_audio_mime':'audio/wav','ref_text':'Original transcript.'}, files={'ref_audio':('ref.wav',wav.getvalue(),'audio/wav')})
        assert created.status_code == 200, created.text
        identifier = created.json()['id']
        Path(OUTPUTS_DIR, 'asset-take.wav').write_bytes(wav.getvalue())
        with db_conn() as conn:
            conn.execute('INSERT INTO generation_history (id,text,audio_path,nimi_voice_asset_id) VALUES (?,?,?,?)',
                ('asset-take', 'A complete locked transcript longer than the old truncation limit. ' * 3, 'asset-take.wav', 'owned-voice-id'))
        assert client.post(f'/profiles/{identifier}/lock', data={'history_id':'asset-take'}).status_code == 200
        init_db()
        locked = _resolve_voice(identifier)
        assert locked['voice_asset_id'] == 'owned-voice-id'
        assert not locked.get('reference_uri')
        assert len(locked['ref_text']) > 100
        assert client.post(f'/profiles/{identifier}/unlock').status_code == 200
        unlocked = _resolve_voice(identifier)
        assert unlocked['reference_uri'] == 'https://example.com/reference.wav'
        assert unlocked['ref_text'] == 'Original transcript.'
        assert not unlocked.get('voice_asset_id')


def test_uri_only_profile_save_has_no_download_or_inference(monkeypatch):
    from fastapi.testclient import TestClient
    import nimi_main
    from business import carrier, reference
    from api.routers.audiobook import _resolve_voice
    async def forbidden(*args, **kwargs):
        raise AssertionError('Saving a URI is business state, not an AI or download action')
    monkeypatch.setattr(carrier, 'call', forbidden)
    monkeypatch.setattr(reference, 'download_reference', forbidden)
    monkeypatch.setattr(nimi_main, '_secret', 'business-unit-test')
    with TestClient(nimi_main.app, headers={'X-VoiceStudio-Worker':'business-unit-test'}) as client:
        result = client.post('/profiles', data={'name':'URI only','ref_audio_uri':'https://example.com/voice.wav','ref_audio_mime':'audio/wav'})
        assert result.status_code == 200, result.text
        profile = result.json()
        assert profile['ref_audio_path'] is None
        voice = _resolve_voice(profile['id'])
        assert voice['ref_audio'] is None
        assert voice['reference_uri'] == 'https://example.com/voice.wav'


def test_media_child_cannot_read_the_business_carrier_stdin():
    import subprocess
    script = """
import asyncio, sys
from services.ffmpeg_utils import run_ffmpeg
async def check():
    code, output, error = await run_ffmpeg([sys.executable, '-c', 'import sys; print("eof=" + sys.stdin.read())'], timeout=1)
    assert code == 0, error
    print(output.decode().strip())
asyncio.run(check())
"""
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2])}
    process = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        # Keep the worker's control pipe OPEN, just as the Electron Host does.
        process.wait(timeout=5)
        output, error = process.communicate()
        assert process.returncode == 0, error.decode(errors='replace')
        assert output.strip() == b'eof='
    finally:
        if process.poll() is None:
            process.kill(); process.communicate()


def test_file_transcription_passes_explicit_language_without_inventing_detection(monkeypatch):
    from fastapi.testclient import TestClient
    import nimi_main
    from business import transcription, media
    calls = []
    async def decode(source, target, **kwargs):
        target.write_bytes(source.read_bytes())
    async def recognize(path, *, language='', **kwargs):
        calls.append(language)
        return {'text':'Transcript contract fixture.', 'language':''}
    monkeypatch.setattr(media, 'decode_to_wav', decode)
    monkeypatch.setattr(transcription, 'transcribe_file', recognize)
    monkeypatch.setattr(nimi_main, '_secret', 'business-unit-test')
    wav = io.BytesIO(); sf.write(wav, np.zeros(2400), 24000, format='WAV', subtype='PCM_16')
    with TestClient(nimi_main.app, headers={'X-VoiceStudio-Worker':'business-unit-test'}) as client:
        for language, expected in [('Auto',''), ('English','en')]:
            result = client.post('/transcribe', data={'language':language,'refine':'false'}, files={'audio':('source.wav',wav.getvalue(),'audio/wav')})
            assert result.status_code == 200, result.text
            assert result.json()['input_language'] == expected
            assert result.json()['language'] == ''
    assert calls == ['', 'en']


def test_live_language_reaches_partial_utterance_and_eof_asr(monkeypatch):
    from fastapi.testclient import TestClient
    import nimi_main
    from business import transcription
    calls = []
    async def recognize(operation, payload):
        assert operation == 'transcribe'
        calls.append(payload)
        return {'text': f'Contract fixture {len(calls)}.', 'language': ''}
    async def no_refinement(text):
        return {}
    monkeypatch.setattr(transcription, 'call', recognize)
    monkeypatch.setattr(transcription, 'refine_text', no_refinement)
    monkeypatch.setenv('VOICESTUDIO_WORKER_SECRET', 'live-language-test')
    monkeypatch.setattr(nimi_main, '_secret', 'live-language-test')
    with TestClient(nimi_main.app) as client:
        for requested, expected in [('English', 'en'), ('Auto', '')]:
            calls.clear()
            with client.websocket_connect('/ws/transcribe?worker_token=live-language-test&language=' + requested) as ws:
                ws.send_bytes(np.full(16000, 4000, dtype='<i2').tobytes())
                assert ws.receive_json()['type'] == 'partial'
                ws.send_bytes(np.zeros(9600, dtype='<i2').tobytes())
                utterance = ws.receive_json()
                assert utterance['final_kind'] == 'utterance'
                ws.send_bytes(np.full(3200, 4000, dtype='<i2').tobytes())
                ws.send_text('EOF')
                tail = ws.receive_json()
                assert tail['final_kind'] == 'utterance'
                summary = ws.receive_json()
                assert summary['final_kind'] == 'summary'
                assert [item['language'] for item in calls] == [expected] * 3
                assert utterance['input_language'] == tail['input_language'] == summary['input_language'] == expected
                assert utterance['language'] == ''


def test_cleanup_disabled_does_not_send_recognized_text_to_any_model(monkeypatch):
    from business import transcription
    from services import refinement
    monkeypatch.setattr(refinement, 'get_refinement_config', lambda: {'auto': False})
    async def forbidden(*args, **kwargs):
        raise AssertionError('Cleanup is off; no status or text model request is needed')
    monkeypatch.setattr(transcription, 'call', forbidden)
    assert asyncio.run(transcription.refine_text('Raw recognition must remain unchanged.')) == {}


def test_provenance_preserves_stereo_samples_and_real_timing():
    samples = np.column_stack([np.linspace(-0.6, 0.6, 1001), np.linspace(0.4, -0.4, 1001)])
    output = io.BytesIO()
    sf.write(output, samples, 24000, format='WAV', subtype='PCM_16')
    original = output.getvalue()
    marked = mark_synthetic(original, context='test')
    with wave.open(io.BytesIO(original)) as before, wave.open(io.BytesIO(marked)) as after:
        assert before.getparams() == after.getparams()
        assert before.readframes(1001) == after.readframes(1001)
    assert b'"synthetic":true' in marked
    assert b'"neuralWatermark":"not-applied"' in marked


def test_subtitle_windows_keep_real_pauses_and_add_offset_once():
    result = _segments([
        {'text': 'Hello.', 'startSeconds': 1.2, 'endSeconds': 1.7},
        {'text': 'Again.', 'startSeconds': 4.1, 'endSeconds': 4.6},
    ], offset=240)
    assert [(row['start'], row['end']) for row in result] == [(241.2, 241.7), (244.1, 244.6)]
    assert result[1]['words'][0]['start'] == 244.1


def test_business_import_does_not_load_inference_libraries():
    import nimi_main
    assert nimi_main.app is not None
    assert not {'torch', 'torchaudio', 'transformers', 'openai', 'demucs'} & sys.modules.keys()


def test_saving_a_designed_voice_preserves_the_auditioned_audio(monkeypatch):
    from fastapi.testclient import TestClient
    import nimi_main
    from business import carrier
    async def unexpected_inference(*args, **kwargs):
        raise AssertionError('Saving an auditioned voice must not generate a different voice')
    monkeypatch.setattr(carrier, 'call', unexpected_inference)
    monkeypatch.setattr(nimi_main, '_secret', 'business-unit-test')
    samples = np.linspace(-0.3, 0.3, 4800, dtype=np.float32)
    output = io.BytesIO()
    sf.write(output, samples, 24000, format='WAV', subtype='PCM_16')
    wav = output.getvalue()
    with TestClient(nimi_main.app, headers={'X-VoiceStudio-Worker': 'business-unit-test'}) as client:
        created = client.post('/profiles', data={'name': 'Audition', 'kind': 'design',
            'vd_states': '{"Gender":"female"}', 'instruct': 'female', 'ref_text': 'The auditioned line.'},
            files={'ref_audio': ('audition.wav', wav, 'audio/wav')})
        assert created.status_code == 200, created.text
        saved = client.get('/profiles/' + created.json()['id'] + '/audio')
        assert saved.status_code == 200
        with wave.open(io.BytesIO(wav)) as original, wave.open(io.BytesIO(saved.content)) as actual:
            assert actual.getparams() == original.getparams()
            assert actual.readframes(4800) == original.readframes(4800)


def test_task_cancel_interrupts_inflight_operation_and_keeps_worker_usable():
    async def scenario():
        init_db()
        manager = TaskManager()
        worker = asyncio.create_task(manager.worker())
        entered, cancelled, second = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def pending():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        async def next_task():
            second.set()
        await manager.add_task('cancel-test', 'test', pending)
        await asyncio.wait_for(entered.wait(), 1)
        assert manager.cancel_task('cancel-test')
        await asyncio.wait_for(cancelled.wait(), 1)
        await manager.add_task('next-test', 'test', next_task)
        await asyncio.wait_for(second.wait(), 1)
        await manager.queue.join()
        assert manager.active_tasks['cancel-test']['status'] == 'cancelled'
        assert manager.active_tasks['next-test']['status'] == 'done'
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())


def test_task_error_event_cannot_be_recorded_as_success():
    async def scenario():
        init_db()
        manager = TaskManager()
        worker = asyncio.create_task(manager.worker())
        async def broken():
            yield 'data: {"type":"error","reason":"source extraction failed"}\n\n'
        await manager.add_task('error-test', 'test', broken)
        await manager.queue.join()
        assert manager.active_tasks['error-test']['status'] == 'failed'
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())


def test_cjk_alignment_does_not_insert_spoken_spaces():
    words = [{'text': text, 'startSeconds': i, 'endSeconds': i+0.4}
             for i, text in enumerate(['你', '好', '，', '世', '界', '。'])]
    assert _segments(words)[0]['text'] == '你好，世界。'


def test_consistent_voice_uses_the_same_real_speaker_clip():
    from business.dubbing import reference_segment
    rows = {'0': {'id':'0','start':0,'end':1,'speaker_id':'a'},
            '1': {'id':'1','start':2,'end':6,'speaker_id':'a'},
            '2': {'id':'2','start':7,'end':12,'speaker_id':'b'}}
    assert reference_segment(rows, '0', '', 'consistent')['id'] == '1'
    assert reference_segment(rows, '1', '', 'consistent')['id'] == '1'
    assert reference_segment(rows, '0', '', 'per_line')['id'] == '0'
    assert reference_segment(rows, '0', 'auto-seg:2', 'per_line')['id'] == '2'


def test_unsupported_expression_tags_fail_before_inference():
    import pytest
    from business.audio import synthesize
    with pytest.raises(ValueError, match='NIMI_EXPRESSION_TAGS'):
        async def bounded():
            return await asyncio.wait_for(synthesize(text='Hello [laughter] world'), 0.2)
        asyncio.run(bounded())

def test_http_disconnect_cancels_business_operation(monkeypatch):
    import socket
    import httpx
    import uvicorn
    import nimi_main
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    async def pending(_form):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
    monkeypatch.setattr(nimi_main, '_secret', 'disconnect-test')
    monkeypatch.setattr(nimi_main, 'generate_take', pending)
    async def scenario():
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(5)
        server = uvicorn.Server(uvicorn.Config(nimi_main.app, log_level='error', lifespan='off', timeout_graceful_shutdown=1))
        running = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient() as client:
                request = asyncio.create_task(client.post(f'http://127.0.0.1:{listener.getsockname()[1]}/generate',
                    headers={'X-VoiceStudio-Worker':'disconnect-test'}, data={'text':'cancel this pending operation'}))
                await asyncio.wait_for(entered.wait(), 2)
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
                await asyncio.wait_for(cancelled.wait(), 1)
        finally:
            server.should_exit = True
            await running
            listener.close()
    asyncio.run(scenario())

def test_batch_design_binding_preserves_saved_audition_and_resolves_presets():
    from core.db import db_conn
    from business.batch import resolve_batch_voice
    from core.config import VOICES_DIR
    init_db()
    source = Path(VOICES_DIR) / 'batch-designed.wav'
    source.parent.mkdir(parents=True, exist_ok=True)
    sf.write(source, np.zeros(2400), 24000)
    with db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles (id,name,kind,ref_audio_path,ref_text,instruct,created_at) VALUES (?,?,?,?,?,?,?)",
                     ('batch-design', 'Batch design', 'design', source.name, 'Audition text.', 'low pitch', 1))
    saved = resolve_batch_voice('batch-design')
    assert saved['ref_audio'] == str(source)
    assert saved['ref_text'] == 'Audition text.'
    preset = resolve_batch_voice('preset:narrator')
    assert not preset['ref_audio']
    assert 'low pitch' in preset['instruct']
    import pytest
    with pytest.raises(ValueError):
        resolve_batch_voice('preset:missing')

def test_conversion_cancel_during_duration_match_does_not_publish_a_take(monkeypatch):
    from starlette.datastructures import UploadFile
    from core.config import VOICES_DIR
    from core.db import db_conn
    from business import audio, media
    init_db()
    source = io.BytesIO(); sf.write(source, np.zeros(12000), 24000, format='WAV')
    rendered = io.BytesIO(); samples = np.zeros((36000, 1), dtype=np.float32)
    sf.write(rendered, samples, 24000, format='WAV')
    reference = Path(VOICES_DIR) / 'conversion-test.wav'; reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(source.getvalue())
    with db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles (id,name,kind,ref_audio_path,ref_text,created_at) VALUES (?,?,?,?,?,?)",
                     ('conversion-test','Conversion target','clone',reference.name,'Source.',1))
        before = conn.execute('SELECT COUNT(*) FROM generation_history').fetchone()[0]
    async def transcribe(*args, **kwargs): return {'text':'Source.', 'language':'en'}
    async def synthesize(**kwargs): return rendered.getvalue(), {'duration':1.5,'sample_rate':24000,'channels':1,'samples':samples}
    monkeypatch.setattr(audio, 'transcribe_file', transcribe)
    monkeypatch.setattr(audio, 'synthesize', synthesize)
    async def scenario():
        matching, disconnected = asyncio.Event(), asyncio.Event()
        async def decode(source_path, target_path, *, filters=None):
            if filters:
                matching.set()
                await asyncio.Future()
            Path(target_path).write_bytes(Path(source_path).read_bytes())
        monkeypatch.setattr(media, 'decode_to_wav', decode)
        class Request:
            async def form(self): return {'audio':UploadFile(io.BytesIO(source.getvalue()),filename='source.wav'), 'profile_id':'conversion-test', 'match_duration':'1'}
            async def is_disconnected(self): return disconnected.is_set()
        operation = asyncio.create_task(media.convert_voice(Request()))
        try:
            await asyncio.wait_for(matching.wait(), 1)
            disconnected.set()
            with __import__('pytest').raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 1)
        finally:
            operation.cancel(); await asyncio.gather(operation, return_exceptions=True)
    asyncio.run(scenario())
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM generation_history').fetchone()[0] == before


def test_batch_delete_uses_dub_cleanup_and_keeps_active_input():
    from api.routers import batch
    from core.config import DUB_DIR, DATA_DIR
    from services.dub_pipeline import save_job
    from fastapi import HTTPException
    import pytest
    init_db()
    directory = Path(DUB_DIR) / 'batch-cleanup-test'; directory.mkdir(parents=True, exist_ok=True)
    output = directory / 'output_es.mp4'; output.write_bytes(b'owned-test-file')
    upload = Path(DATA_DIR) / 'batch-cleanup-upload.mp4'; upload.write_bytes(b'owned-upload')
    save_job('batch-cleanup-test', {'segments':[], 'dubbed_tracks':{}, 'duration':1}, filename='test.mp4')
    batch._jobs['batch-cleanup-test'] = {'status':'running', 'video_path':str(upload)}
    try:
        with pytest.raises(HTTPException) as error: batch.delete_batch_job('batch-cleanup-test')
        assert error.value.status_code == 409 and upload.exists()
        batch._jobs['batch-cleanup-test']['status'] = 'done'
        assert batch.delete_batch_job('batch-cleanup-test')['deleted']
        assert not directory.exists() and not upload.exists()
    finally:
        batch._jobs.pop('batch-cleanup-test',None)

def test_cancelled_queued_retry_runs_once_at_the_tail(monkeypatch):
    from api.routers import batch
    from business import batch as pipeline, carrier
    from starlette.datastructures import UploadFile
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def execute(job_id, job):
            calls.append(job['filename'])
            if job['filename'] == 'a.mp4':
                entered.set(); await release.wait()
        async def status(*args, **kwargs):
            return {'effectiveSelections':[{'capabilityContract':name,'state':'ready'} for name in ['audio.transcribe','audio.synthesize','voice.create','text.generate']]}
        monkeypatch.setattr(pipeline, 'run_batch', execute)
        monkeypatch.setattr(carrier, 'call', status)
        monkeypatch.setattr(batch, '_queue', None); monkeypatch.setattr(batch, '_worker_task', None)
        monkeypatch.setattr(batch, '_jobs', {}); monkeypatch.setattr(batch, '_running_tasks', {})
        monkeypatch.setattr(batch, '_processing_job_ids', set())
        async def enqueue(name):
            return await batch.enqueue_batch_job(video=UploadFile(io.BytesIO(b'queue-only fixture'), filename=name),
                langs='es', voice_id=None, preserve_bg=False, translation_provider=None)
        try:
            await enqueue('a.mp4'); await asyncio.wait_for(entered.wait(), 1)
            b = await enqueue('b.mp4'); await enqueue('c.mp4')
            await batch.cancel_batch_job(b['job_id'])
            await batch.retry_batch_job(b['job_id'])
            release.set(); await asyncio.wait_for(batch._queue.join(), 1)
            assert calls == ['a.mp4','c.mp4','b.mp4']
            assert batch._jobs[b['job_id']]['attempts'] == 2
        finally:
            release.set()
            if batch._worker_task:
                batch._worker_task.cancel(); await asyncio.gather(batch._worker_task, return_exceptions=True)
    asyncio.run(scenario())

def test_longform_cancel_marks_job_cancelled_and_keeps_resume_manifest(monkeypatch):
    from api.routers import audiobook
    from services import ffmpeg_utils, longform_resume
    from services.audiobook import parse_audiobook_script
    init_db()
    async def scenario():
        entered = asyncio.Event()
        async def pending(*args, **kwargs):
            entered.set(); await asyncio.Future()
        monkeypatch.setattr(audiobook, '_run_chapter', pending)
        monkeypatch.setattr(ffmpeg_utils, 'find_ffmpeg', lambda: 'not-executed-in-cancellation-test')
        async def consume():
            async for _ in audiobook._render_longform_sse(parse_audiobook_script('# One\nPending narration.'),
                default_voice=None, job_id='longform-cancel-test'):
                pass
        operation = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 1)
        operation.cancel(); await asyncio.gather(operation, return_exceptions=True)
        row = next(row for row in job_store.list_jobs() if row['id'] == 'longform-cancel-test')
        assert row['status'] == 'cancelled'
        assert longform_resume.work_dir('audiobook','longform-cancel-test') is not None
        assert longform_resume.has_manifest('audiobook','longform-cancel-test')
    asyncio.run(scenario())

def test_saved_preset_reference_survives_job_reload_without_new_design(monkeypatch):
    from business import dubbing
    from services import dub_pipeline
    from core.config import DUB_DIR
    init_db()
    directory = Path(DUB_DIR)/'preset-reload-test'; directory.mkdir(parents=True,exist_ok=True)
    reference = directory/'preset-narrator.wav'; sf.write(reference,np.zeros(2400),24000)
    conditioning = {'ref_audio':str(reference),'ref_text':'Saved audition.','instruct':'low pitch','seed':None}
    dub_pipeline.save_job('preset-reload-test', {'segments':[], 'preset_voices':{'preset:narrator':conditioning}})
    dub_pipeline._dub_jobs.pop('preset-reload-test',None)
    loaded = dub_pipeline.get_job('preset-reload-test')
    async def unexpected(**kwargs): raise AssertionError('A saved preset must not be redesigned')
    monkeypatch.setattr(dubbing,'synthesize',unexpected)
    assert asyncio.run(dubbing.preset_conditioning('preset-reload-test',loaded,'preset:narrator','New line.','fr')) == conditioning
    reference.unlink()
    import pytest
    with pytest.raises(ValueError,match='audition is missing'):
        asyncio.run(dubbing.preset_conditioning('preset-reload-test',loaded,'preset:narrator','New line.','fr'))
