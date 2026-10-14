import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CLONE_MAX_SECONDS,
  REF_HARD_MAX_SECONDS,
  generateClone,
  parseGenerateHeaders,
  toGenerateForm,
} from './generate';
import type { CloneGenerateInput } from './types';

const BASE_INPUT: CloneGenerateInput = {
  text: 'Hello there',
  language: 'Auto',
  steps: 16,
  cfg: 2,
  speed: 1,
  tShift: 0.1,
  posTemp: 5,
  classTemp: 0,
  layerPenalty: 5,
  denoise: true,
  postprocess: true,
  duration: '',
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('constants', () => {
  it('exposes the clip limits', () => {
    expect(CLONE_MAX_SECONDS).toBe(15);
    expect(REF_HARD_MAX_SECONDS).toBe(75);
  });
});

describe('toGenerateForm', () => {
  it('sends supported business controls and omits old engine sampling controls', () => {
    const form = toGenerateForm({ ...BASE_INPUT, profileId: 'p1' });
    expect(form.get('text')).toBe('Hello there');
    expect(form.has('language')).toBe(false);
    expect(form.get('num_step')).toBeNull();
    expect(form.get('guidance_scale')).toBeNull();
    expect(form.get('speed')).toBe('1');
    expect(form.get('denoise')).toBe('true');
    expect(form.get('t_shift')).toBeNull();
    expect(form.get('position_temperature')).toBeNull();
    expect(form.get('class_temperature')).toBeNull();
    expect(form.get('layer_penalty_factor')).toBeNull();
    expect(form.get('postprocess_output')).toBe('true');
    expect(form.has('duration')).toBe(false);
    expect(form.has('instruct')).toBe(false);
  });

  it('sends explicit language and omits unsupported requested duration', () => {
    const form = toGenerateForm({
      ...BASE_INPUT,
      language: 'French',
      duration: '3.5',
      profileId: 'p1',
    });
    expect(form.get('language')).toBe('French');
    expect(form.get('duration')).toBeNull();
  });

  it('sends profile_id XOR ref_audio (profile wins)', () => {
    const clip = new File([new Uint8Array(4)], 'clip.wav', { type: 'audio/wav' });
    const withProfile = toGenerateForm({ ...BASE_INPUT, profileId: 'p1', refAudio: clip });
    expect(withProfile.get('profile_id')).toBe('p1');
    expect(withProfile.has('ref_audio')).toBe(false);
    expect(withProfile.has('ref_text')).toBe(false);

    const withClip = toGenerateForm({
      ...BASE_INPUT,
      profileId: null,
      refAudio: clip,
      refText: 'the transcript',
    });
    expect(withClip.has('profile_id')).toBe(false);
    const sent = withClip.get('ref_audio');
    expect(sent).toBeInstanceOf(File);
    expect((sent as File).name).toBe('clip.wav');
    expect(withClip.get('ref_text')).toBe('the transcript');
  });

  it('uses refAudioName for a bare Blob', () => {
    const blob = new Blob([new Uint8Array(4)], { type: 'audio/webm' });
    const form = toGenerateForm({ ...BASE_INPUT, refAudio: blob, refAudioName: 'rec.webm' });
    expect((form.get('ref_audio') as File).name).toBe('rec.webm');
  });

  it('preserves Nimi voice descriptions beyond the retired engine tag list', () => {
    expect(
      toGenerateForm({ ...BASE_INPUT, profileId: 'p1', instruct: 'whisper, gentle' }).get(
        'instruct',
      ),
    ).toBe('whisper, gentle');
    expect(
      toGenerateForm({ ...BASE_INPUT, instruct: 'A calm adult English narrator.' }).get('instruct'),
    ).toBe('A calm adult English narrator.');
  });
});

describe('parseGenerateHeaders', () => {
  it('reads every take header', () => {
    const headers = new Headers({
      'X-Audio-Id': 'abc',
      'X-Audio-Path': 'abc.wav',
      'X-Audio-Duration': '3.25',
      'X-Gen-Time': '1.5',
      'X-Seed': '42',
      'X-OmniVoice-Routing': 'cpu_fallback',
      'X-OmniVoice-Routing-Reason': 'no CUDA',
      'X-OmniVoice-Dropped-Chunks': '2',
      'X-OmniVoice-Dropped-Text': 'lost | words',
    });
    expect(parseGenerateHeaders(headers)).toEqual({
      id: 'abc',
      audioPath: 'abc.wav',
      durationSeconds: 3.25,
      genTimeSeconds: 1.5,
      seed: 42,
      routing: { status: 'cpu_fallback', reason: 'no CUDA' },
      dropped: { count: 2, text: 'lost | words' },
    });
  });

  it('yields nulls when headers are absent or malformed', () => {
    const headers = new Headers({ 'X-Seed': 'nope', 'X-OmniVoice-Dropped-Chunks': '0' });
    expect(parseGenerateHeaders(headers)).toEqual({
      id: null,
      audioPath: null,
      durationSeconds: null,
      genTimeSeconds: null,
      seed: null,
      routing: null,
      dropped: null,
    });
  });
});

describe('generateClone', () => {
  it('posts the form, streams the WAV with progress and parses the headers', async () => {
    const bytes = new Uint8Array(1000);
    const fetchMock = vi.fn(
      async () =>
        new Response(bytes, {
          status: 200,
          headers: {
            'Content-Type': 'audio/wav',
            'Content-Length': String(bytes.length),
            'X-Audio-Id': 'take-1',
            'X-Audio-Path': 'take-1.wav',
            'X-Audio-Duration': '2',
            'X-Gen-Time': '0.4',
            'X-Seed': '7',
          },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const onProgress = vi.fn();

    const result = await generateClone({ ...BASE_INPUT, profileId: 'p1' }, { onProgress });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/generate');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('profile_id')).toBe('p1');
    expect(init.signal).toBeInstanceOf(AbortSignal);

    expect(result.blob.size).toBe(bytes.length);
    expect(result.blob.type).toBe('audio/wav');
    expect(result.id).toBe('take-1');
    expect(result.audioPath).toBe('take-1.wav');
    expect(result.durationSeconds).toBe(2);
    expect(result.genTimeSeconds).toBe(0.4);
    expect(result.seed).toBe(7);
    expect(result.routing).toBeNull();
    expect(result.dropped).toBeNull();
    expect(onProgress).toHaveBeenCalled();
    expect(onProgress).toHaveBeenLastCalledWith(100);
  });

  it('surfaces a backend error as ApiError with its detail', async () => {
    vi.stubGlobal(
      'fetch',
      async () => new Response(JSON.stringify({ detail: 'Unsupported instruct' }), { status: 400 }),
    );
    await expect(generateClone({ ...BASE_INPUT, profileId: 'p1' })).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      detail: 'Unsupported instruct',
    });
  });

  it('aborts when the caller signal fires', async () => {
    vi.stubGlobal(
      'fetch',
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          );
        }),
    );
    const controller = new AbortController();
    const pending = generateClone(
      { ...BASE_INPUT, profileId: 'p1' },
      { signal: controller.signal },
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('aborts on its own after the backstop timeout', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          );
        }),
    );
    const pending = generateClone({ ...BASE_INPUT, profileId: 'p1' });
    const assertion = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(21 * 60 * 1000 + 1);
    await assertion;
  });
});
