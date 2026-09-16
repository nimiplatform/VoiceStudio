// @vitest-environment node
import { createServer as createHttpServer } from 'node:http';
import { createServer } from 'vite';
import { expect, it } from 'vitest';
import { startDevBackendProxy } from './dev-backend-proxy';

it('propagates a completed request body cancellation through both development proxies', async () => {
  let received!: () => void;
  let disconnected!: () => void;
  const entered = new Promise<void>(r => { received = r; });
  const closed = new Promise<void>(r => { disconnected = r; });
  const worker = createHttpServer((request, response) => {
    request.resume();
    request.on('end', received);
    response.on('close', disconnected);
  });
  await new Promise<void>(r => worker.listen(0, '127.0.0.1', r));
  const port = (worker.address() as {port:number}).port;
  const bridge = await startDevBackendProxy(() => `http://127.0.0.1:${port}`, () => ({}), 0);
  const vite = await createServer({ configFile: false, root: process.cwd(), logLevel: 'silent',
    server: { host: '127.0.0.1', port: 0, proxy: { '/api': { target: bridge.url } } } });
  try {
    await vite.listen();
    const vitePort = (vite.httpServer!.address() as {port:number}).port;
    const controller = new AbortController();
    const result = fetch(`http://127.0.0.1:${vitePort}/api/generate`, {method:'POST',body:'request',signal:controller.signal}).catch(e=>e);
    await entered;
    controller.abort();
    await result;
    const propagated = await Promise.race([closed.then(()=>true),new Promise<boolean>(r=>setTimeout(()=>r(false),750))]);
    expect(propagated).toBe(true);
  } finally {
    await vite.close(); await bridge.close(); worker.closeAllConnections();
    await new Promise<void>(r=>worker.close(()=>r()));
  }
});
