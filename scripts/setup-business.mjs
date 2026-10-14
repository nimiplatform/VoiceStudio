import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const localUv = path.join(root, '.nimi/local/python-tools/Scripts/uv.exe');
const uv = localUv;
const pythonHome = path.join(root, '.nimi/local/python');
const python = path.join(pythonHome, 'cpython-3.12.14-windows-x86_64-none/python.exe');
if (process.platform !== 'win32' || process.arch !== 'x64') {
  throw new Error('The current business-worker build has been prepared for Windows x64. Other hosts require a matched Python build.');
}
if (!existsSync(localUv)) {
  const toolRoot = path.dirname(path.dirname(localUv));
  for (const [command, args] of [
    ['python', ['-m', 'venv', toolRoot]],
    [path.join(toolRoot, 'Scripts/python.exe'), ['-m', 'pip', 'install', 'uv==0.12.15']],
  ]) {
    const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', windowsHide: true });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`Business tool setup failed (${result.status})`);
  }
}
function run(args) {
  const result = spawnSync(uv, args, { cwd: root, env: { ...process.env, UV_PYTHON_INSTALL_DIR: pythonHome }, stdio: 'inherit', windowsHide: true });
  if (result.error) throw new Error('Install uv before running setup:business.', { cause: result.error });
  if (result.status !== 0) throw new Error(`Business dependency setup failed (${result.status})`);
}
if (!existsSync(python)) run(['python', 'install', '3.12.14', '--no-bin']);
run(['sync', '--project', 'backend/business', '--python', python, '--frozen']);
