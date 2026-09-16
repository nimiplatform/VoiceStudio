# VoiceStudio for Nimi

VoiceStudio 0.5.4 adapts the existing [VoiceStudio](https://github.com/debpalash/VoiceStudio) Electron product to Nimi. Clone and design voices, make stories and audiobooks, transcribe recordings, and dub video while retaining the original editing and media workflows.

Nimi supplies the platform account, App Access, AI configuration and all model inference. VoiceStudio keeps its business orchestration, audio editing, media processing, voice library and project state. There is no separate VoiceStudio account. Choose models in **Settings → Nimi**; no automatic cloud fallback or App-managed model installation is used.

## Development

The current complete package combination is `dev-ecd93c916-voicestudio`: app-tools 0.7.0, SDK 0.14.0, Kit/native 0.10.0 and nimi-coding 0.6.3. The delivered tarball paths are explicit in `pnpm-workspace.yaml`, with integrity pins in `pnpm-lock.yaml`. Same-version package replacements require a normal install and updated lockfile. This checkout uses Node 24 and pnpm 10.34.5.

```powershell
pnpm install --frozen-lockfile
pnpm run setup:business
pnpm exec nimi-app sync
pnpm exec nimi-app check
pnpm exec nimi-app dev --shell electron
```

Development also requires `uv` and a Rust toolchain for the native helper. Desktop and Runtime must be running. Use the exact supervised App CDP URL printed by app-tools. Opening the Vite URL alone does not establish a Nimi session. Use this supervised development path for ordinary changes. Rebuild/install only for changes to frozen resources, production protocols, installation/upgrades or installed data retention. Stop the supervised App before running engineering tests: source fixtures created by tests can trigger a Host rebuild.

The independent Python 3.12.14 environment uses `backend/business/uv.lock`; it contains deterministic business/media dependencies, not Torch or model engines. Setup is explicit. Normal launch never installs dependencies. The original standalone instructions are archived in [README.upstream.md](README.upstream.md) and do not describe this distribution.

## Build and local installation

```powershell
pnpm exec nimi-app test
pnpm exec nimi-app build --target windows-x86_64
pnpm exec nimi-app pack --target windows-x86_64 --production
```

The production pipeline bundles the existing renderer, protected Electron Host, frozen Python business worker, native desktop helper and FFmpeg/ffprobe. Import the resulting `.nimiapp` with Nimi Desktop and grant App Access. Windows is the first actual acceptance target. Other platforms, Catalog download and public publication are separate and have not been verified.

Versions 0.5.3 and 0.5.4 are local adaptation repair updates: Desktop correctly requires a newer version for each installed update. These version changes do not publish a release.

## Data and privacy

Existing SQLite data, voice recordings and outputs remain in the App data directory (`%APPDATA%/OmniVoice` by default). Packaged Electron browser state uses `%APPDATA%/OmniVoice/electron-profile`; development uses Desktop's supervised profile. Story/audiobook projects can be exported and imported as `.voicestudio.json`. Those files reference voices and audio in the App library; they do not embed the media or transfer Nimi credentials. No automatic developer-profile migration or user-data deletion is performed.

Analytics remains opt-in. AI routing follows the user's Nimi configuration, so local-only behavior depends on that choice. Synthesized WAVs pass through `mark_synthetic` and carry file metadata identifying synthetic content. Neural AudioSeal marking is not applied by this integration; metadata can be removed by external re-encoding.

## Current acceptance

| Product task | Verified scope |
| --- | --- |
| Reference voice / cloning | Saved voice and transcript; real Nimi voice creation and synthesis; completed artifact recovery after carrier repair; App playback; identity lock; native WAV export |
| Voice design | Real description-based design and synthesis; App playback; saved the audition itself as the voice reference |
| Stories / audiobooks | Two saved voices, editable lines, saved project, interrupted-run recovery, real MP3 and M4B render and playback; native MP3 export; M4B chapter timestamps verified; project JSON export/import passed |
| File / windowed transcription | Real file ASR and Nimi text refinement; manual correction, reload retention and native TXT export. Microphone capture start/pause/resume/cancel passed. Real PCM sample replay returned partial, utterance and EOF; this is not a human-microphone recognition result |
| Video dubbing / translation / export | Real separation and timed ASR; source and Spanish translation editing; consistent reference voice; two synthesized segments; video playback; native MP4/SRT export. The default exported track matches the Spanish mix |
| Batch and additional audio tools | Implemented; NOT-VERIFIED |
| Windows production artifact | Official build and production pack passed; frozen worker health and UTF-8 voice persistence passed |
| Desktop installed 0.5.4 | Normal update from 0.5.3, launch, App Access and business API passed. Existing history played; a new reference-voice “Welcome back.” take completed through Nimi, played and saved as WAV. Project JSON imported, opened and saved; after a normal Desktop stop/start, both voices, three history entries, the new export and the reopened mixed-voice project remained available |

Actual samples and local acceptance scratch are under `.nimi/local/` and excluded from Git. Playback verification establishes decodable, non-muted media reaching playback end; voice quality still needs human listening.

### Platform changes and limits

The same-package reference verified protected session, App Access, AIConfig, voice creation, synthesis and Desktop package installation before product integration. Shared platform work added the design/reference voice-library recipe, repaired Node artifact-stream EOF cleanup and documented storage adoption's normalized filename. Runtime `322a8fde1` fixed Gemma JSON-object Driver mapping by supplying an explicit object schema; the unchanged product translation request then passed strict completion validation. The App uses the returned `asset.relativePath` for reads and cleanup. Kit `b279ac4e1` fixes origin derivation for Electron custom schemes while retaining exact renderer URL validation; it addresses the first installed launch rejecting `app://voicestudio`. The App also registers its own scheme and Kit’s public asset descriptor in a single Electron call; a real hidden Electron regression verifies GET and POST through the production protocol, because a second registration disabled fetch while leaving IPC available.

Qwen3 Base reference synthesis uses a saved reference voice. It does not consume speech-style instructions or OmniVoice expression tokens. Speech speed is applied by App-owned FFmpeg processing. Voice design descriptions belong to voice creation. Previews use cancellable finite Jobs; continuous TTS streaming is not claimed. Dictation recognizes buffered audio windows, not a native low-latency stream. For the real 5.15-second replay, the final utterance reported 13.766 seconds for ASR plus text refinement on the current model/machine; no low-latency guarantee is made. Human speech recognition from a microphone and global shortcut insertion remain NOT-VERIFIED. Multi-speaker identification requires manual assignment until a matching public Nimi contract is available. A real admitted synthesis Job reached CANCELED after the App cancellation action (2026-09-16 01:54:52 UTC); previous history stayed unchanged and the original draft was restored. This regression exposed and fixed a Python HTTP middleware disconnect issue; worker authentication now preserves the original ASGI receive channel. Interrupted story recovery was tested with the actual saved manifest. Batch, additional timing strategies, long documents, cloud routes, account switching and cross-platform builds remain NOT-VERIFIED.

Engineering verification: app-tools `check` and `test` passed; the test command covers Electron type checking, all 21 locales, renderer/Host tests, Python business and mechanical checks with an empty offline HF cache, the native helper, and a real isolated Electron protocol GET/POST regression (not installed product acceptance). The final run passed 522 Electron tests, 545 Python/business/mechanical checks and 30 native tests. Windows `build --target windows-x86_64` and `pack --target windows-x86_64 --production` passed. The artifact is `dist/nimi-app/nimiplatform.voicestudio-0.5.4-windows-x86_64.nimiapp`; its generated `.target.json` holds the size, digest and observed unsigned production posture. Desktop local update, launch and the agreed installed business/data-retention checks passed on Windows. The installed short take is available at `.nimi/local/exports/installed-welcome.wav` (PCM16, 24 kHz, mono, 0.8 seconds). The App was left open on the restored project. Catalog download, public publication and other target machines remain NOT-VERIFIED; this work does not claim those results.

## Support

For missing AI access or model configuration, check Desktop App Access and **Settings → Nimi**. For an App startup failure, restart from Desktop and retain the visible error, App version and affected action for the fork maintainer. Keep the disclosed data directory intact; deleting voices or projects is not a repair step. Do not include credentials in diagnostic material.

## License and upstream

This fork retains the original AGPL-3.0 license, notices and contributor attribution. Adaptation began at upstream baseline `4e55180f700e2ce1b39195ec7377b9b3da8e20b2`. Archived upstream workflows and installation material are historical references, not active Nimi release instructions. This work does not publish a release or submit a Catalog entry.
