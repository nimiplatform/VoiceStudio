import { describe, expect, it, vi } from 'vitest';
import type { RegisteredNimiElectronAppBridge } from '@nimiplatform/kit/shell/electron/main';
import { NimiJobs } from './nimi-jobs';

describe('Nimi business artifact custody', () => {
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
