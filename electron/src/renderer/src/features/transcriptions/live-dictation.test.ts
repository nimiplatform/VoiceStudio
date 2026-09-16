import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ api: vi.fn(), capture: vi.fn(), bridge: null as any }));
vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api }));
vi.mock('@/hooks/use-backend-status', () => ({
  getBackendStatusSnapshot: () => ({ baseUrl: 'http://127.0.0.1:3900' }),
}));
vi.mock('@/components/bridge', () => ({ getBridge: () => mocks.bridge }));
vi.mock('../../../../../../frontend/src/utils/aec/micCapture', () => ({
  startMicCapture: mocks.capture,
}));
import { LiveDictation } from './live-dictation';

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = 3;
    this.onclose?.();
  });
  constructor(readonly url: URL) {
    Socket.instances.push(this);
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen?.();
    });
  }
  frame(value: object) {
    this.onmessage?.({ data: JSON.stringify(value) });
  }
}
let live: LiveDictation;
let stopTrack: ReturnType<typeof vi.fn>;
let stopGraph: ReturnType<typeof vi.fn>;
let microphone: ReturnType<typeof vi.fn>;
let emit: (samples: Float32Array) => void;
beforeEach(() => {
  localStorage.clear();
  mocks.bridge = null;
  Socket.instances = [];
  vi.stubGlobal('WebSocket', Socket);
  stopTrack = vi.fn();
  stopGraph = vi.fn(async () => {});
  microphone = vi.fn(async (_constraints: MediaStreamConstraints) => ({
    getTracks: () => [{ stop: stopTrack }],
  }));
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: microphone } });
  mocks.api.mockResolvedValue({ready:true});
  mocks.capture.mockImplementation(async (_stream: MediaStream, callback: typeof emit) => {
    emit = callback;
    return stopGraph;
  });
  live = new LiveDictation();
});
it('uses the shared language for page and widget sessions, captured before microphone permission', async () => {
  localStorage.setItem('voicestudio.dictation-settings.v1', JSON.stringify({ inputLanguage: 'English' }));
  let allow!: (stream: { getTracks: () => { stop: typeof stopTrack }[] }) => void;
  microphone.mockImplementationOnce(() => new Promise(resolve => { allow = resolve; }));
  const starting = live.start(vi.fn());
  await vi.waitFor(() => expect(microphone).toHaveBeenCalled());
  localStorage.setItem('voicestudio.dictation-settings.v1', JSON.stringify({ inputLanguage: 'Chinese' }));
  allow({ getTracks: () => [{ stop: stopTrack }] });
  await starting;
  expect(Socket.instances[0].url.searchParams.get('language')).toBe('English');
  live.cancel();
  const widget = new LiveDictation();
  try {
    await widget.start(vi.fn());
    expect(Socket.instances[1].url.searchParams.get('language')).toBe('Chinese');
  } finally { widget.cancel(); }
});

it('keeps automatic language empty on the protected WebSocket', async () => {
  await live.start(vi.fn());
  expect(Socket.instances[0].url.searchParams.get('language')).toBe('');
});
afterEach(() => {
  live.cancel();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it('checks installation before requesting microphone access', async () => {
  mocks.api.mockResolvedValue({
    enabled: true,
    model_id: 'missing',
    engine_available: true,
    models: [],
  });
  await live.start(vi.fn());
  expect(live.getSnapshot().issue).toBe('model');
  expect(microphone).not.toHaveBeenCalled();
  expect(Socket.instances).toHaveLength(0);
});
it('streams PCM and preserves repeated utterances without duplicating the EOF summary', async () => {
  const result = vi.fn();
  await live.start(result, 'studio-mic');
  const socket = Socket.instances[0];
  expect(socket.url.searchParams.get('model')).toBeNull();
  expect(microphone.mock.calls[0]?.[0]).toMatchObject({
    audio: { deviceId: { exact: 'studio-mic' } },
  });
  emit(new Float32Array([-1, 1]));
  expect(Array.from(new Int16Array(socket.send.mock.calls[0][0]))).toEqual([-32768, 32767]);
  socket.frame({ type: 'partial', text: 'Hello' });
  expect(live.getSnapshot().text).toBe('Hello');
  socket.frame({ type: 'final', final_kind: 'utterance', text: 'Hello' });
  socket.frame({ type: 'final', final_kind: 'utterance', text: 'Hello' });
  socket.frame({ type: 'final', final_kind: 'summary', text: 'Hello Hello' });
  expect(result).toHaveBeenCalledTimes(2);
  expect(live.getSnapshot().stage).toBe('done');
  expect(stopTrack).toHaveBeenCalled();
  expect(stopGraph).toHaveBeenCalled();
});
it('releases late microphone permission after cancellation', async () => {
  let allow!: (value: { getTracks: () => { stop: typeof stopTrack }[] }) => void;
  microphone.mockImplementation(
    () =>
      new Promise((resolve) => {
        allow = resolve;
      }),
  );
  const pending = live.start(vi.fn());
  await vi.waitFor(() => expect(microphone).toHaveBeenCalled());
  live.cancel();
  allow({ getTracks: () => [{ stop: stopTrack }] });
  await pending;
  expect(stopTrack).toHaveBeenCalledOnce();
  expect(Socket.instances).toHaveLength(0);
  expect(live.getSnapshot().stage).toBe('idle');
});
it('ignores old socket callbacks after another session starts', async () => {
  const stale = vi.fn();
  await live.start(stale);
  const callback = Socket.instances[0].onmessage!;
  live.cancel();
  const current = vi.fn();
  await live.start(current);
  callback({ data: JSON.stringify({ type: 'final', final_kind: 'summary', text: 'old' }) });
  expect(stale).not.toHaveBeenCalled();
  expect(current).not.toHaveBeenCalled();
  expect(live.getSnapshot().stage).toBe('recording');
});
it('pauses transmission and waits for a final result after EOF', async () => {
  const result = vi.fn();
  await live.start(result);
  const socket = Socket.instances[0];
  live.pause();
  emit(new Float32Array([1]));
  expect(socket.send).not.toHaveBeenCalled();
  live.pause();
  emit(new Float32Array([1]));
  expect(socket.send).toHaveBeenCalledOnce();
  await live.stop();
  expect(socket.send).toHaveBeenLastCalledWith('EOF');
  expect(live.getSnapshot().stage).toBe('transcribing');
  socket.frame({ type: 'final', final_kind: 'summary', text: 'Finished.' });
  expect(result).toHaveBeenCalledWith(expect.objectContaining({ text: 'Finished.' }));
  expect(live.getSnapshot().stage).toBe('done');
});
it('keeps text available when storing a result fails', async () => {
  await live.start(() => {
    throw new Error('quota');
  });
  Socket.instances[0].frame({ type: 'final', final_kind: 'summary', text: 'Keep this.' });
  expect(live.getSnapshot()).toMatchObject({
    stage: 'error',
    issue: 'storage',
    text: 'Keep this.',
  });
});

it('accepts terminal results from a legacy PCM fallback', async () => {
  const result = vi.fn();
  await live.start(result);
  await live.stop();
  Socket.instances[0].frame({ type: 'final', text: 'Fallback transcript.' });
  expect(result).toHaveBeenCalledWith(expect.objectContaining({ text: 'Fallback transcript.' }));
  expect(live.getSnapshot().stage).toBe('done');
});
it('still sends EOF when audio graph cleanup rejects', async () => {
  await live.start(vi.fn());
  stopGraph.mockRejectedValueOnce(new Error('Already closed'));
  await expect(live.stop()).resolves.toBeUndefined();
  expect(Socket.instances[0].send).toHaveBeenLastCalledWith('EOF');
});

it('uses the dev WebSocket proxy even when the native bridge supplies a backend URL', async () => {
  vi.stubGlobal('window', {
    location: { href: 'http://localhost:3912/#/capture', protocol: 'http:' },
  });
  await live.start(vi.fn());
  expect(Socket.instances[0].url.origin).toBe('ws://localhost:3912');
  expect(Socket.instances[0].url.pathname).toBe('/api/ws/transcribe');
});
it('uses the Host-issued business URL from the packaged app scheme', async () => {
  mocks.bridge={backend:{websocketUrl:vi.fn(async()=>"ws://127.0.0.1:3900/ws/transcribe?worker_token=test")}};
  vi.stubGlobal('window', {
    location: { href: 'app://voicestudio/index.html#/capture', protocol: 'app:' },
  });
  await live.start(vi.fn());
  expect(Socket.instances[0].url.origin).toBe('ws://127.0.0.1:3900');
  expect(Socket.instances[0].url.pathname).toBe('/ws/transcribe');
});
it('preserves the main-issued business-worker token', async () => {
  mocks.bridge = {
    backend: {
      websocketUrl: vi.fn(
        async () => `ws://127.0.0.1:3900/ws/transcribe?worker_token=ovs_worker_token_${'b'.repeat(43)}`,
      ),
    },
  };
  vi.stubGlobal('window', {
    location: { href: 'app://voicestudio/index.html#/capture', protocol: 'app:' },
    voicestudio: mocks.bridge,
  });
  await live.start(vi.fn());
  expect(Socket.instances[0].url.origin).toBe('ws://127.0.0.1:3900');
  expect(Socket.instances[0].url.searchParams.get('worker_token')).toMatch(/^ovs_worker_token_/);
  expect(Socket.instances[0].url.searchParams.get('model')).toBeNull();
});

it('deduplicates raw EOF against raw utterances while preserving the refined display', async () => {
  const result = vi.fn();
  await live.start(result);
  const socket = Socket.instances[0];
  socket.frame({ type: 'final', final_kind: 'utterance', text: 'meet pat at 3', refined_text: 'Meet Pat at three.' });
  socket.frame({ type: 'final', final_kind: 'summary', text: 'meet pat at 3' });
  expect(result).toHaveBeenCalledTimes(1);
  expect(live.getSnapshot()).toMatchObject({ stage: 'done', text: 'Meet Pat at three.' });
});

it('retains a backend Job failure and does not replace it with a later socket close', async () => {
  await live.start(vi.fn());
  const socket = Socket.instances[0];
  const closed = socket.onclose!;
  socket.frame({ type: 'error', message: 'speech-transcribe job 01M2MS32QDMK76GY125Z7GVTYG: ai-output-invalid: provider returned invalid output' });
  closed();
  expect(live.getSnapshot()).toMatchObject({ stage: 'error', issue: 'transcription',
    errorDetail: expect.stringContaining('01M2MS32QDMK76GY125Z7GVTYG: ai-output-invalid') });
});

it('keeps microphone failure details and redacts credentials rather than blaming a model', async () => {
  microphone.mockRejectedValueOnce(new DOMException('Device failed; worker_token=private-value', 'NotReadableError'));
  await live.start(vi.fn());
  expect(live.getSnapshot()).toMatchObject({ issue: 'microphone',
    errorDetail: expect.stringContaining('NotReadableError') });
  expect(JSON.stringify(live.getSnapshot())).not.toContain('private-value');
});
