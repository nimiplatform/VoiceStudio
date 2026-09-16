import { startMicCapture } from '../../../../../../frontend/src/utils/aec/micCapture';
import { subscribeFarEnd } from '../../../../../../frontend/src/utils/aec/farEndBus';
import {
  AEC_FAR,
  AEC_NEAR,
  floatToInt16,
  tagFrame,
} from '../../../../../../frontend/src/utils/aec/pcm';
import {
  classifySherpaFinal,
  sherpaSummaryTail,
} from '../../../../../../frontend/src/utils/captureProtocol';
import type { TranscriptEntry } from '../../../../../../frontend/src/utils/transcriptionsStore';
import { apiJson } from '@/lib/api/client';
import { backendWebSocketUrl } from '@/lib/api/websocket';
import { beginAppActivity } from '@/lib/app-activity';
import { getAecEnabled, getDictationInputLanguage } from '@/lib/store/dictation-settings';
import { scrubText } from '../../../../../../frontend/src/utils/scrub';
import { tr } from '@/lib/i18n-text';

export interface DictationState {
  stage: 'idle' | 'starting' | 'recording' | 'transcribing' | 'done' | 'error';
  text: string;
  modelStage?: string;
  issue?: 'model' | 'microphone' | 'connection' | 'transcription' | 'storage';
  paused: boolean;
  inputLanguage?: string;
  errorDetail?: string;
}
interface Frame extends Partial<TranscriptEntry> {
  type: string;
  final_kind?: string;
  stage?: string;
  model_silent?: boolean;
  message?: string;
}

/** One microphone/WS session; stale async completions cannot publish into a later one. */
export class LiveDictation {
  private state: DictationState = { stage: 'idle', text: '', paused: false };
  private listeners = new Set<() => void>();
  private generation = 0;
  private request: AbortController | null = null;
  private stream: MediaStream | null = null;
  private socket: WebSocket | null = null;
  private stopGraph: (() => Promise<void>) | null = null;
  private unsubscribeFarEnd: (() => void) | null = null;
  private deadline: ReturnType<typeof setTimeout> | null = null;
  private committed: string[] = [];
  private rawCommitted: string[] = [];
  private onResult: (entry: Partial<TranscriptEntry>) => void = () => {};
  private startedAt = 0;
  private finishActivity: (() => void) | null = null;

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<DictationState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  async start(
    onResult: (entry: Partial<TranscriptEntry>) => void,
    deviceId?: string,
  ): Promise<void> {
    if (['starting', 'recording', 'transcribing'].includes(this.state.stage)) return;
    // The page and global widget use one persisted choice. Freeze it for this
    // session, including time spent waiting for microphone permission.
    const inputLanguage = getDictationInputLanguage();
    this.release();
    this.finishActivity = beginAppActivity('dictation');
    const generation = ++this.generation;
    const current = () => generation === this.generation;
    const request = new AbortController();
    this.request = request;
    this.onResult = onResult;
    this.committed = [];
    this.rawCommitted = [];
    this.publish({
      stage: 'starting',
      inputLanguage,
      text: '',
      issue: undefined,
      errorDetail: undefined,
      modelStage: undefined,
      paused: false,
    });
    let issue: DictationState['issue'] = 'connection';
    try {
      const readiness = await apiJson<{ ready: boolean }>('/dictation/readiness', { signal: request.signal });
      if (!current()) return;
      if (!readiness.ready) { this.fail('model'); return; }
      issue = 'microphone';
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        },
      });
      if (!current()) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      issue = 'connection';
      const url = new URL(await backendWebSocketUrl('/ws/transcribe'));
      url.searchParams.set('pcm', '1');
      url.searchParams.set('sr', '16000');
      url.searchParams.set('language', inputLanguage === 'Auto' ? '' : inputLanguage);
      const aecEnabled = getAecEnabled();
      if (aecEnabled) url.searchParams.set('aec', '1');
      const socket = new WebSocket(url);
      this.socket = socket;
      socket.onmessage = (event) => {
        if (!current()) return;
        try {
          this.frame(JSON.parse(String(event.data)) as Frame);
        } catch (error) {
          this.fail('transcription', error);
        }
      };
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(tr('transcriptions.live_error_connection') + ' [timeout: 10000ms]')), 10_000);
        const finish = (error?: Error) => {
          clearTimeout(timer);
          request.signal.removeEventListener('abort', aborted);
          if (error) reject(error);
          else resolve();
        };
        const aborted = () => finish(new Error('Cancelled'));
        request.signal.addEventListener('abort', aborted, { once: true });
        socket.onopen = () => finish();
        socket.onerror = () => finish(new Error(tr('transcriptions.live_error_connection')));
        socket.onclose = (event) => finish(new Error(event?.reason || (event?.code ? `WebSocket close ${event.code}` : tr('transcriptions.live_error_connection'))));
      });
      if (!current()) return;
      socket.onerror = () => {
        if (current()) this.fail('connection');
      };
      socket.onclose = (event) => {
        if (current() && this.state.stage !== 'done')
          this.fail('connection', event?.reason || (event?.code ? `WebSocket close ${event.code}` : undefined));
      };
      issue = 'microphone';
      const stop = await startMicCapture(
        stream,
        (frame) => {
          if (!current() || this.state.paused || this.state.stage !== 'recording') return;
          if (socket.readyState !== WebSocket.OPEN) {
            this.fail('connection');
            return;
          }
          if (socket.bufferedAmount > 1024 * 1024) {
            this.fail('connection', tr('transcriptions.live_error_backpressure'));
            return;
          }
          const pcm = floatToInt16(frame);
          socket.send(aecEnabled ? tagFrame(pcm, AEC_NEAR) : pcm.buffer);
        },
        { sampleRate: 16000, channels: 1 },
      );
      if (!current()) {
        await stop();
        return;
      }
      this.stopGraph = stop;
      if (aecEnabled) {
        this.unsubscribeFarEnd = subscribeFarEnd((frame) => {
          if (!current() || this.state.stage !== 'recording') return;
          if (socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 1024 * 1024) return;
          socket.send(tagFrame(floatToInt16(frame), AEC_FAR));
        });
      }
      this.startedAt = Date.now();
      this.publish({ stage: 'recording' });
    } catch (error) {
      if (current()) this.fail(issue, error);
    }
  }

  pause(): void {
    if (this.state.stage === 'recording') this.publish({ paused: !this.state.paused });
  }
  async stop(): Promise<void> {
    if (this.state.stage !== 'recording') return;
    const generation = this.generation;
    this.publish({ stage: 'transcribing', paused: false });
    const stop = this.stopGraph;
    this.stopGraph = null;
    this.unsubscribeFarEnd?.();
    this.unsubscribeFarEnd = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    await stop?.().catch(() => {});
    if (generation !== this.generation) return;
    if (this.socket?.readyState !== WebSocket.OPEN) {
      this.fail('connection');
      return;
    }
    this.socket.send('EOF');
    this.deadline = setTimeout(
      () => this.fail('transcription', tr('transcriptions.live_error_timeout')),
      Math.min(120_000, Math.max(15_000, Date.now() - this.startedAt + 10_000)),
    );
  }

  cancel(): void {
    this.generation++;
    this.release();
    this.publish({
      stage: 'idle',
      text: '',
      paused: false,
      modelStage: undefined,
      issue: undefined,
      errorDetail: undefined,
    });
  }
  private frame(message: Frame): void {
    if (message.type === 'status') {
      this.publish({
        modelStage: message.stage === 'ready' ? undefined : message.stage,
      });
      return;
    }
    if (message.type === 'error' || (message.model_silent && !message.text?.trim())) {
      this.fail('transcription', message.message || (message.model_silent ? tr('capture.no_speech') : undefined));
      return;
    }
    if (message.type === 'partial') {
      this.publish({
        text: [...this.committed, message.text || ''].filter(Boolean).join(' '),
      });
      return;
    }
    if (message.type !== 'final') return;
    // Legacy PCM fallback sends one terminal final without final_kind.
    const kind = message.final_kind === undefined ? 'summary' : classifySherpaFinal(message);
    const text = message.refined_text || message.text || '';
    if (kind === 'utterance') {
      this.committed.push(text);
      this.rawCommitted.push(message.text || '');
      this.publish({ text: this.committed.join(' ') });
      try {
        this.onResult(message);
      } catch (error) {
        this.fail('storage', error);
      }
      return;
    }
    if (kind === 'ignore') return;
    const tail = sherpaSummaryTail(message.text || '', this.rawCommitted);
    this.publish({ text: this.committed.length ? [...this.committed, tail].filter(Boolean).join(' ') : text });
    try {
      if (tail)
        this.onResult(
          this.committed.length
            ? { ...message, text: tail, refined_text: undefined, segments: [] }
            : message,
        );
    } catch (error) {
      this.fail('storage', error);
      return;
    }
    this.generation++;
    this.release();
    this.publish({ stage: 'done', modelStage: undefined, paused: false });
  }
  private fail(issue: NonNullable<DictationState['issue']>, cause?: unknown): void {
    const error = cause && typeof cause === 'object' ? cause as { name?: unknown; message?: unknown } : null;
    const raw = typeof cause === 'string' ? cause : typeof error?.message === 'string'
      ? `${typeof error.name === 'string' && error.name !== 'Error' ? error.name + ': ' : ''}${error.message}` : '';
    const detail = scrubText(raw).replace(/\s+/g, ' ').trim().slice(0, 2000)
      || (issue === 'microphone' ? tr('recording.mic_error', { message: tr('common.error') })
        : tr('transcriptions.live_error_' + issue));
    // Class/Job identity are useful in the existing log ring; never log PCM,
    // recognized text, a raw exception stack or the worker's socket URL.
    const job = detail.match(/speech-transcribe job ([A-Za-z0-9_-]{1,64}):/)?.[1];
    console.warn(`[VoiceStudio dictation] ${issue}${job ? ' job=' + job : ''}`);
    this.generation++;
    this.release();
    this.publish({
      stage: 'error',
      issue,
      errorDetail: detail,
      modelStage: undefined,
      paused: false,
    });
  }
  private release(): void {
    this.finishActivity?.();
    this.finishActivity = null;
    this.request?.abort();
    this.request = null;
    if (this.deadline) clearTimeout(this.deadline);
    this.deadline = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    const stop = this.stopGraph;
    this.stopGraph = null;
    this.unsubscribeFarEnd?.();
    this.unsubscribeFarEnd = null;
    void stop?.().catch(() => {});
    if (this.socket) {
      this.socket.onmessage = null;
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.close();
      this.socket = null;
    }
  }
}
