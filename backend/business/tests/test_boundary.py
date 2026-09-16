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
