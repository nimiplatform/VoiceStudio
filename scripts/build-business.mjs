import { spawnSync } from 'node:child_process';
import { cp, realpath, rm } from 'node:fs/promises';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, windowsHide: true, stdio: 'inherit', env: { ...process.env, PYTHONUTF8: '1' } });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited ${result.status}`);
}
run('cargo', ['build', '--manifest-path', 'native/desktop-bridge/Cargo.toml', '--locked', '--release']);
run(process.execPath, ['scripts/prepare-business-resources.mjs']);
run(path.join(root, 'backend/business/.venv/Scripts/python.exe'), ['-m', 'PyInstaller', 'backend/nimi-business.spec',
  '--distpath', '.nimi/local/business-dist', '--workpath', '.nimi/local/business-build', '--noconfirm']);
const target = path.join(root, '.nimi/local/production-resources/business');
const actual = await realpath(target);
if (!actual.toLowerCase().startsWith((root + path.sep).toLowerCase())) throw new Error('Business payload leaves the project');
await rm(target, { recursive: true, force: true });
await cp(path.join(root, '.nimi/local/business-dist/voicestudio-business'), target, { recursive: true });
