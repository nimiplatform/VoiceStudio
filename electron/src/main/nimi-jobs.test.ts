import { describe, expect, it, vi } from 'vitest';
import type { RegisteredNimiElectronAppBridge } from '@nimiplatform/kit/shell/electron/main';
import { NimiJobs } from './nimi-jobs';

describe('Nimi business artifact custody', () => {
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
