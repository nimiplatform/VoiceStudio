import { build } from 'esbuild';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
const local = path.join(root, '.nimi/local');
await mkdir(local, { recursive: true });
const directory = await mkdtemp(path.join(local, 'protocol-test-'));
try {
  const entry = path.join(directory, 'probe.mjs');
  await build({ entryPoints: [path.join(root, 'electron/tests/protocol-probe.ts')], outfile: entry,
    bundle: true, platform: 'node', format: 'esm', packages: 'external' });
  const require = createRequire(import.meta.url);
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const code = await new Promise((resolve, reject) => {
    const child = spawn(require('electron'), [entry, directory], { cwd: root, env, windowsHide: true, stdio: 'inherit' });
    child.once('error', reject);
    child.once('exit', code => resolve(code));
  });
  if (code !== 0) throw new Error(`Electron protocol regression failed (${code}).`);
} finally {
  if (path.dirname(directory) !== local || !path.basename(directory).startsWith('protocol-test-')) throw new Error('Unexpected protocol test path.');
  await rm(directory, { recursive: true, force: true });
}
