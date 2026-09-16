import { mkdir, copyFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const resources = path.join(root, '.nimi/local/production-resources');
for (const dir of ['business', 'brand', 'native', 'media']) await mkdir(path.join(resources, dir), { recursive: true });
for (const file of ['32x32.png', '128x128.png', 'icon.png', 'icon.ico'])
  await copyFile(path.join(root, 'frontend/src-tauri/icons', file), path.join(resources, 'brand', file));
await copyFile(path.join(root, 'native/desktop-bridge/target/release/voicestudio-desktop-bridge.exe'),
  path.join(resources, 'native/voicestudio-desktop-bridge.exe'));
const python = path.join(root, 'backend/business/.venv/Scripts/python.exe');
const script = "import sys;sys.path.insert(0,'backend');from services import media_tools;result=media_tools.acquire_bundled(wait=True);assert result['state']=='done', result;print(media_tools.bundled_dir())";
const acquired = spawnSync(python, ['-c', script], { cwd: root, encoding: 'utf8', windowsHide: true,
  env: { ...process.env, PYTHONUTF8: '1', OMNIVOICE_DATA_DIR: path.join(root, '.nimi/local/build-data') } });
if (acquired.status !== 0) throw new Error(acquired.stderr || acquired.error?.message || 'Media acquisition failed');
const media = acquired.stdout.trim().split(/\r?\n/).at(-1);
for (const file of ['ffmpeg.exe', 'ffprobe.exe']) await copyFile(path.join(media, file), path.join(resources, 'media', file));
console.log('VoiceStudio native and media resources prepared.');
