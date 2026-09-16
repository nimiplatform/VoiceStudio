import { spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
const local = path.join(root, '.nimi/local');
await mkdir(local, { recursive: true });
const cache = await mkdtemp(path.join(local, 'test-hf-'));
const python = path.join(root, 'backend/business/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
try {
  const result = spawnSync(python, ['-m', 'pytest', 'backend/business/tests', 'tests/test_app_version.py',
    'tests/test_locale_parity.py', 'tests/test_no_hardcoded_cjk.py', '-q'], {
    cwd: root, windowsHide: true, stdio: 'inherit',
    env: { ...process.env, PYTHONUTF8: '1', HF_HUB_OFFLINE: '1', HF_HUB_CACHE: cache },
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} finally {
  if (path.dirname(cache) !== local || !path.basename(cache).startsWith('test-hf-')) throw new Error('Unexpected test cache path');
  await rm(cache, { recursive: true, force: true });
}
