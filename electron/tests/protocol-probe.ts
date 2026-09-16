import { app, BrowserWindow } from 'electron';
import { createServer } from 'node:http';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { registerAppScheme, installAppProtocol } from '../src/main/protocol';

// A real hidden Chromium/HTTP transport regression; this does not exercise Nimi AI.
const directory = process.argv.at(-1)!;
const profile = path.join(directory, 'profile');
mkdirSync(profile);
app.setPath('userData', profile);
writeFileSync(path.join(directory, 'index.html'), '<html><body>Protocol regression</body></html>');
registerAppScheme();
const timer = setTimeout(() => { console.error('Electron protocol regression timed out'); app.exit(1); }, 30_000);
app.whenReady().then(async () => {
  const worker = createServer((request, response) => {
    const bytes: Buffer[] = [];
    request.on('data', chunk => bytes.push(chunk));
    request.on('end', () => {
      response.writeHead(request.headers['x-voicestudio-worker'] === 'protocol-test-only' ? 200 : 403,
        { 'content-type': 'application/json' });
      response.end(JSON.stringify({ path: request.url, method: request.method, body: Buffer.concat(bytes).toString('utf8') }));
    });
  });
  await new Promise<void>(resolve => worker.listen(0, '127.0.0.1', resolve));
  const port = (worker.address() as { port: number }).port;
  installAppProtocol(() => `http://127.0.0.1:${port}`, directory, () => ({ 'X-VoiceStudio-Worker': 'protocol-test-only' }));
  const window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  try {
    await window.loadURL('app://voicestudio/index.html');
    const result = await window.webContents.executeJavaScript(`(async () => {
      const get = await fetch('/api/health');
      const post = await fetch('/api/echo', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: 'VoiceStudio É' }) });
      return { origin: location.origin, get: { status: get.status, body: await get.json() }, post: { status: post.status, body: await post.json() } };
    })()`);
    assert.equal(result.origin, 'app://voicestudio');
    assert.equal(result.get.status, 200);
    assert.equal(result.get.body.path, '/health');
    assert.equal(result.post.status, 200);
    assert.equal(JSON.parse(result.post.body.body).text, 'VoiceStudio É');
    console.log('Electron installed-scheme GET/POST proxy regression passed.');
  } finally {
    window.destroy();
    worker.closeAllConnections();
    await new Promise<void>(resolve => worker.close(() => resolve()));
    clearTimeout(timer);
  }
  app.quit();
}).catch(error => { console.error(error); clearTimeout(timer); app.exit(1); });
