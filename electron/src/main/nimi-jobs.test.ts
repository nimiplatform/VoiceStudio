import { describe, expect, it, vi } from 'vitest';
import type { RegisteredNimiElectronAppBridge } from '@nimiplatform/kit/shell/electron/main';
import { NimiJobs } from './nimi-jobs';

describe('Nimi business artifact custody', () => {
  it.each([[1, 24000], [2, 48000], [2, 44100]])('submits separation using canonical owned audio (%i channels, %i Hz)', async (channels, rate) => {
    const upload = vi.fn(async (input) => ({ artifactId: input.source ? 'stereo-input' : 'prepared-input',
      audioInfo: { channels: input.source ? 2 : channels, sampleRateHz: input.source ? 44100 : rate, frameCount: rate, durationMs: 1000 } }));
    const submit = vi.fn(async () => ({ job: { jobId: 'separation-job' } }));
    const services = {
      ai: { artifacts: { upload }, scenarioJobs: { submit, get: async () => ({ job: { status: 'completed',
        audioSeparation: { vocalsArtifactId: 'vocals', backgroundArtifactId: 'background' } } }) } },
      storage: { assets: { adoptArtifact: async ({ artifactId }: { artifactId: string }) => ({ relativePath: `${artifactId}.wav` }),
        read: async () => ({ body: (async function* () { yield new Uint8Array([1]); })() }), remove: async () => ({}) } },
    } as unknown as RegisteredNimiElectronAppBridge['services'];
    await new NimiJobs(services).run('separate', { base64: 'AQ==', mimeType: 'audio/wav' }, new AbortController().signal);
    expect(Array.from(upload.mock.calls[0][0].bytes)).toEqual([1]);
    expect(upload.mock.calls[0][0]).toMatchObject({ mimeType: 'audio/wav',
      audioPreparation: { profile: 'canonical-pcm-v1' } });
    const converted = channels === 1 || rate !== 44100;
    expect(upload).toHaveBeenCalledTimes(converted ? 2 : 1);
    if (converted) expect(upload.mock.calls[1][0]).toMatchObject({ source: { kind: 'artifact', artifactId: 'prepared-input' },
      audioPreparation: { targetSampleRateHz: 44100, channelMode: channels === 1 ? 'MONO_TO_STEREO' : 'PRESERVE' } });
    expect(submit).toHaveBeenCalledWith({ type: 'audio-separate', mimeType: 'audio/wav',
      sourceAudio: { artifactId: converted ? 'stereo-input' : 'prepared-input' } });
  });

  it('does not submit separation after cancellation during audio preparation', async () => {
    const controller = new AbortController();
    const submit = vi.fn();
    const services = { ai: { artifacts: { upload: async () => {
      controller.abort();
      return { artifactId: 'prepared-input', audioInfo: { channels: 2 } };
    } }, scenarioJobs: { submit } } } as unknown as RegisteredNimiElectronAppBridge['services'];
    await expect(new NimiJobs(services).run('separate', { base64: 'AQ==', mimeType: 'audio/wav' }, controller.signal)).rejects.toThrow();
    expect(submit).not.toHaveBeenCalled();
  });

  it('maps URI transcripts from capability metadata and reuses an owned saved asset', async () => {
    const submit = vi.fn(async (spec) => ({ job: { jobId: spec.type } }));
    const get = vi.fn(async (id) => id === 'voice-create'
      ? { job: { status: 'completed' }, voiceReference: { voiceAssetId: 'voice-owned' }, asset: { status: 'active' } }
      : { job: { jobId: 'synth-job', status: 'completed', artifacts: [{ artifactId: 'audio', mimeType: 'audio/wav' }] } });
    const services = {
      aiConfig: { get: vi.fn(async () => ({ effectiveSelections: [{ capabilityContract: 'voice.create', resource: {
        oneofKind: 'cloud', cloud: { target: { referenceAudioInput: { supportsBytes: false, supportsUri: true, textMode: 'unsupported', mimeTypes: ['audio/wav'] } } },
      } }] })) },
      ai: { scenarioJobs: { submit, get, cancel: vi.fn() } },
      storage: { assets: { adoptArtifact: async () => ({ relativePath: 'audio.wav' }),
        read: async () => ({ body: (async function* () { yield new Uint8Array([1]); })() }), remove: async () => ({}) } },
    } as unknown as RegisteredNimiElectronAppBridge['services'];
    const host = new NimiJobs(services);
    const signal = new AbortController().signal;
    const result = await host.run('synthesize', { text: 'New speech.', reference: {
      uri: 'https://example.com/audio.wav', mimeType: 'audio/wav', text: 'Business transcript.',
    } }, signal);
    expect(submit.mock.calls[0][0]).toMatchObject({ referenceAudio: { type: 'uri', uri: 'https://example.com/audio.wav' }, text: '' });
    expect(result).toMatchObject({ voiceAssetId: 'voice-owned' });
    submit.mockClear();
    await host.run('synthesize', { text: 'Another line.', voiceAssetId: 'voice-owned' }, signal);
    expect(submit).toHaveBeenCalledTimes(1);
    expect(submit.mock.calls[0][0]).toMatchObject({ type: 'speech-synthesize', voiceRef: { type: 'voice-asset', id: 'voice-owned' } });
    submit.mockClear();
    await expect(host.run('synthesize', { text: 'No upload.', reference: { base64: 'AQ==', mimeType: 'audio/wav', text: '' } }, signal)).rejects.toThrow('public audio URL');
    expect(submit).not.toHaveBeenCalled();
  });
  it('reads and removes the canonical path returned by adoption', async () => {
    const read = vi.fn(async () => ({ body: (async function* () { yield new Uint8Array([1, 2, 3]); })() }));
    const remove = vi.fn(async () => ({ removed: true }));
    const services = {
      ai: { scenarioJobs: { get: vi.fn(async () => ({ job: { scenarioType: 'speech-synthesize', status: 'completed', artifacts: [{ artifactId: 'artifact', mimeType: 'audio/wav' }] } })) } },
      storage: { assets: { adoptArtifact: vi.fn(async () => ({ relativePath: 'transfers/artifact.wav', mediaType: 'audio/wav' })), read, remove } },
    } as unknown as RegisteredNimiElectronAppBridge['services'];
    const result = await new NimiJobs(services).run('recover-speech', { jobId: 'completed-job' }, new AbortController().signal);
    expect(read).toHaveBeenCalledWith({ relativePath: 'transfers/artifact.wav' });
    expect(remove).toHaveBeenCalledWith('transfers/artifact.wav');
    expect(result).toMatchObject({ base64: 'AQID', mimeType: 'audio/wav', jobId: 'completed-job' });
  });

  it('cannot recover an unfinished or non-speech job as audio', async () => {
    const adoptArtifact = vi.fn();
    const services = { ai: { scenarioJobs: { get: vi.fn(async () => ({ job: { scenarioType: 'speech-synthesize', status: 'running' } })) } },
      storage: { assets: { adoptArtifact } } } as unknown as RegisteredNimiElectronAppBridge['services'];
    await expect(new NimiJobs(services).run('recover-speech', { jobId: 'running-job' }, new AbortController().signal)).rejects.toThrow('completed');
    expect(adoptArtifact).not.toHaveBeenCalled();
  });
});
