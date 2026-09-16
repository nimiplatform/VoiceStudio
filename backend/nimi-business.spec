# VoiceStudio's deterministic business process; inference stays in the Nimi Host.
from pathlib import Path
root = Path(SPECPATH).parent
datas = [(str(root / 'alembic.ini'), '.'), (str(root / 'backend/migrations'), 'backend/migrations'),
         (str(root / 'frontend/package.json'), 'frontend'),
         (str(root / 'pyproject.toml'), '.'), (str(root / 'backend/assets'), 'assets')]
a = Analysis([str(root / 'backend/nimi_main.py')], pathex=[str(root), str(root / 'backend')],
    datas=datas, binaries=[], hiddenimports=['soundfile', 'soxr', 'uvicorn.logging', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.websockets_impl', 'uvicorn.lifespan.on'],
    excludes=['torch', 'torchaudio', 'transformers', 'openai', 'demucs', 'whisperx', 'faster_whisper',
              'services.model_manager', 'services.asr_backend', 'services.tts_backend', 'services.llm_backend'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='voicestudio-business', console=True)
coll = COLLECT(exe, a.binaries, a.datas, name='voicestudio-business')
