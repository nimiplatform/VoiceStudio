import { spawn, type ChildProcess } from 'node:child_process';
import { createInterface } from 'node:readline';
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { app } from 'electron';
import type { BackendStatus } from '../preload/index.d';
import type { NimiJobs } from './nimi-jobs';

export function backendRoot(): string {
  return app.isPackaged ? process.resourcesPath : resolve(dirname(fileURLToPath(import.meta.url)), '..');
}

/** Supervises only VoiceStudio's bundled business and media worker. */
export class BackendSupervisor {
  readonly port = Number(process.env.OMNIVOICE_PORT) || 3900;
  readonly baseUrl = `http://127.0.0.1:${this.port}`;
  readonly connection = { remote: false, url: this.baseUrl, authenticated: true };
  readonly ownedRuntimeRoot = null;
  private secret = randomBytes(32).toString('hex');
  private child: ChildProcess | null = null;
  private tasks = new Map<string, AbortController>();
  private listeners = new Set<(status: BackendStatus) => void>();
  private generation = 0;
  private startedAt = 0;
  private value: BackendStatus = {
    stage: 'idle', baseUrl: this.baseUrl, port: this.port, managed: false,
    remote: false, elapsedMs: 0, logTail: [],
  };
  constructor(private readonly jobs: NimiJobs) {}
  get status(): BackendStatus { return { ...this.value, elapsedMs: this.startedAt ? Date.now() - this.startedAt : 0 }; }
  requestHeaders(): Record<string, string> { return { 'X-VoiceStudio-Worker': this.secret }; }
  subscribe(listener: (status: BackendStatus) => void) {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }
  acknowledgeLastCrash() { return this.status; }
  private publish(value: Partial<BackendStatus>) {
    this.value = { ...this.value, ...value };
    for (const listener of this.listeners) listener(this.status);
  }
  private log(line: string) {
    this.publish({ logTail: [...this.value.logTail, line].slice(-40) });
  }
  async start(): Promise<void> {
    if (this.child) return;
    const generation = ++this.generation;
    this.startedAt = Date.now();
    this.publish({ stage: 'starting', message: undefined });
    const root = backendRoot();
    const executable = app.isPackaged
      ? join(root, 'business', 'voicestudio-business.exe')
      : join(root, 'backend', 'business', '.venv', 'Scripts', 'python.exe');
    if (!existsSync(executable)) {
      this.publish({ stage: 'failed', message: 'VoiceStudio business worker is missing. Run pnpm run setup:business.' });
      return;
    }
    const args = app.isPackaged ? [] : [join(root, 'backend', 'nimi_main.py')];
    const env: NodeJS.ProcessEnv = { PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
      VOICESTUDIO_WORKER_SECRET: this.secret, OMNIVOICE_PORT: String(this.port) };
    for (const key of ['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA',
      'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'PROGRAMDATA', 'OMNIVOICE_DATA_DIR']) {
      if (process.env[key]) env[key] = process.env[key];
    }
    const media = app.isPackaged ? join(root, 'media') : join(root, '.nimi/local/production-resources/media');
    if (existsSync(join(media, 'ffmpeg.exe'))) env.FFMPEG_PATH = join(media, 'ffmpeg.exe');
    if (existsSync(join(media, 'ffprobe.exe'))) env.FFPROBE_PATH = join(media, 'ffprobe.exe');
    const child = spawn(executable, args, { cwd: root, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    this.child = child;
    child.stdin?.on('error', () => {});
    createInterface({ input: child.stderr! }).on('line', (line) => this.log(line));
    createInterface({ input: child.stdout! }).on('line', (line) => {
      if (!line.startsWith('{')) { this.log(line); return; }
      void this.callback(line, child).catch((error) => this.log(String(error)));
    });
    child.on('error', (error) => {
      if (generation === this.generation) this.publish({ stage: 'failed', message: error.message });
    });
    child.on('exit', (code) => {
      this.cancelTasks();
      if (this.child === child) this.child = null;
      if (generation === this.generation)
        this.publish({ stage: 'crashed', managed: false, exitCode: code, message: `Business worker exited (${code})` });
    });
    for (let attempt = 0; attempt < 120 && generation === this.generation; attempt++) {
      try {
        const response = await fetch(`${this.baseUrl}/health`, { headers: this.requestHeaders(), signal: AbortSignal.timeout(1000) });
        if (response.ok && (await response.json() as { service?: string }).service === 'voicestudio-business') {
          this.publish({ stage: 'ready', managed: true });
          return;
        }
      } catch { /* The child owns its startup error output. */ }
      if (child.exitCode !== null) return;
      await delay(500);
    }
    if (generation === this.generation) this.publish({ stage: 'failed', message: 'VoiceStudio business worker did not become ready.' });
  }
  cancelTasks(): void {
    for (const controller of this.tasks.values()) controller.abort();
  }
  private async callback(line: string, child: ChildProcess): Promise<void> {
    const message = JSON.parse(line) as { id: string; operation?: string; input?: unknown; cancel?: boolean };
    if (typeof message.id !== 'string') throw new Error('Invalid worker callback');
    if (message.cancel) { this.tasks.get(message.id)?.abort(); return; }
    if (!message.operation) throw new Error('Missing worker operation');
    const controller = new AbortController();
    this.tasks.set(message.id, controller);
    let reply;
    try {
      const result = await this.jobs.run(message.operation, message.input, controller.signal);
      controller.signal.throwIfAborted();
      reply = { id: message.id, result };
    } catch (error) {
      reply = { id: message.id, error: error instanceof Error ? error.message : String(error) };
    } finally { this.tasks.delete(message.id); }
    if (this.child === child && child.stdin?.writable)
      child.stdin.write(Buffer.from(JSON.stringify(reply) + '\n', 'utf8'));
  }
  async websocketUrl(path: string): Promise<string> {
    if (!['/ws/transcribe', '/ws/events'].includes(path)) throw new Error('Unsupported business socket');
    return `${this.baseUrl.replace('http:', 'ws:')}${path}?worker_token=${encodeURIComponent(this.secret)}`;
  }
  async shutdown(): Promise<void> {
    ++this.generation;
    this.cancelTasks();
    const child = this.child;
    this.child = null;
    child?.stdin?.end();
    if (child && child.exitCode === null) {
      await Promise.race([new Promise((done) => child.once('exit', done)), delay(3000)]);
      if (child.exitCode === null) child.kill();
    }
    this.publish({ stage: 'idle', managed: false });
  }
  async restart(): Promise<void> { await this.shutdown(); await this.start(); }
}
