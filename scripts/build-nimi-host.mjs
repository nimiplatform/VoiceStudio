import { build } from 'esbuild';
import { readFile } from 'node:fs/promises';
const { version } = JSON.parse(await readFile('frontend/package.json', 'utf8'));
const production = process.argv.includes('--production');
await build({
  entryPoints: ['electron/src/main/index.ts'], outfile: 'dist-electron/main.js',
  bundle: true, packages: 'external', platform: 'node', target: 'node22', format: 'esm',
  define: { __APP_VERSION__: JSON.stringify(version), __NIMI_ELECTRON_PRODUCTION__: String(production) },
});
await build({
  entryPoints: ['electron/src/preload/index.ts'], outfile: 'dist-electron/preload.cjs',
  bundle: true, external: ['electron'], platform: 'node', target: 'node22', format: 'cjs',
  define: { __APP_VERSION__: JSON.stringify(version) },
});
