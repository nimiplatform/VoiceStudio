import { createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { createNimiLocalAppTextModel } from '@nimiplatform/sdk/ai';
import type { RegisteredNimiElectronAppBridge } from '@nimiplatform/kit/shell/electron/main';
import type { NimiLocalAppScenarioJobSpec } from '@nimiplatform/sdk/app';

type Services = RegisteredNimiElectronAppBridge['services'];
type SpeechInput = {
  text: string; language?: string; speed?: number; instruction?: string;
  reference?: { base64: string; mimeType: string; text: string };
};
type AudioInput = { base64: string; mimeType: string; language?: string; timestamps?: boolean };

/** App business operations over the one protected Host. Never accepts routes or credentials. */
export class NimiJobs {
  private pending = new Set<AbortController>();
  private voices = new Map<string, string>();
  constructor(private readonly services: Services) {}

  invalidate(): void {
    for (const request of this.pending) request.abort();
    this.voices.clear();
  }

  async run(operation: string, input: unknown, signal: AbortSignal): Promise<unknown> {
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
    this.pending.add(controller);
    try {
      controller.signal.throwIfAborted();
      if (!input || typeof input !== 'object') throw new Error('Invalid business input');
      switch (operation) {
        case 'recover-speech': {
          const { jobId } = input as { jobId: string };
          if (typeof jobId !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(jobId)) throw new Error('Invalid speech job');
          const result = await this.services.ai.scenarioJobs.get(jobId);
          if (result.job.scenarioType !== 'speech-synthesize' || result.job.status !== 'completed')
            throw new Error('Only a completed speech job can be recovered');
          const audio = result.job.artifacts.find((item) => item.mimeType.startsWith('audio/'));
          if (!audio) throw new Error('Completed speech job has no audio');
          return { ...await this.artifact(audio.artifactId, controller.signal), jobId };
        }
        case 'status': {
          const snapshot = await this.services.aiConfig.get();
          return {
            fingerprint: createHash('sha256').update(JSON.stringify(snapshot.effectiveSelections)).digest('hex'),
            effectiveSelections: snapshot.effectiveSelections.map(({ capabilityContract, state, reasons }) => ({ capabilityContract, state, reasons })),
          };
        }
        case 'synthesize': return await this.synthesize(input as SpeechInput, controller.signal);
        case 'transcribe': return await this.transcribe(input as AudioInput, controller.signal);
        case 'translate': {
          const { text, instruction, json } = input as { text: string; instruction: string; json?: boolean };
          if (typeof text !== 'string' || typeof instruction !== 'string') throw new Error('Invalid translation input');
          const result = await createNimiLocalAppTextModel(this.services.ai).generateText({
            messages: [
              { role: 'system', content: [{ type: 'text', text: instruction }] },
              { role: 'user', content: [{ type: 'text', text }] },
            ],
            signal: controller.signal,
            ...(json ? { responseFormat: { type: 'json-object' as const } } : {}),
          });
          controller.signal.throwIfAborted();
          return { text: result.text };
        }
        case 'separate': {
          const audio = input as AudioInput;
          const result = await this.job({ type: 'audio-separate', mimeType: audio.mimeType,
            audioSource: { type: 'bytes', bytes: this.audioBytes(audio) } }, controller.signal);
          const pair = result.job.audioSeparation;
          if (!pair) throw new Error('Nimi did not return a complete separation result');
          const vocals = await this.artifact(pair.vocalsArtifactId, controller.signal);
          const background = await this.artifact(pair.backgroundArtifactId, controller.signal);
          return { vocals, background };
        }
        default: throw new Error(`Unknown VoiceStudio business operation: ${operation}`);
      }
    } finally {
      this.pending.delete(controller);
      signal.removeEventListener('abort', abort);
    }
  }

  private audioBytes(input: AudioInput): number[] {
    if (typeof input.base64 !== 'string' || typeof input.mimeType !== 'string')
      throw new Error('Audio bytes and MIME type are required');
    const bytes = Buffer.from(input.base64, 'base64');
    if (!bytes.length || bytes.length > 32 * 1024 * 1024) throw new Error('Audio must be between 1 byte and 32 MiB');
    return Array.from(bytes);
  }

  private async job(spec: NimiLocalAppScenarioJobSpec, signal: AbortSignal) {
    signal.throwIfAborted();
    const submitted = await this.services.ai.scenarioJobs.submit(spec);
    const id = submitted.job.jobId;
    console.info(`[Nimi ${spec.type}] submitted ${id}`);
    let terminal = false;
    const cancel = () => { void this.services.ai.scenarioJobs.cancel(id, 'VoiceStudio task cancelled').catch(() => {}); };
    signal.addEventListener('abort', cancel, { once: true });
    try {
      if (signal.aborted) { cancel(); signal.throwIfAborted(); }
      for (;;) {
        const result = await this.services.ai.scenarioJobs.get(id);
        signal.throwIfAborted();
        if (result.job.status === 'completed') { terminal = true; return result; }
        if (['failed', 'canceled', 'timeout'].includes(result.job.status)) {
          terminal = true;
          throw new Error(`${result.job.reasonCode}: ${result.job.reasonDetail || result.job.status}`);
        }
        await delay(400, undefined, { signal });
      }
    } catch (error) {
      if (!terminal) await this.services.ai.scenarioJobs.cancel(id, 'VoiceStudio could not finish receiving this job').catch(() => {});
      throw new Error(`${spec.type} job ${id}: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
    } finally { signal.removeEventListener('abort', cancel); }
  }

  private async artifact(id: string, signal: AbortSignal) {
    signal.throwIfAborted();
    const relativePath = `transfers/${id}`;
    const stage = async <T>(name: string, work: () => Promise<T>): Promise<T> => {
      try { return await work(); }
      catch (error) { throw new Error(`Artifact ${id} ${name}: ${error instanceof Error ? error.message : String(error)}`, { cause: error }); }
    };
    const asset = await stage('adopt', () => this.services.storage.assets.adoptArtifact({ artifactId: id, relativePath, overwrite: true }));
    try {
      return await stage('read', async () => {
      const result = await stage('open read stream', () => this.services.storage.assets.read({ relativePath: asset.relativePath }));
      const chunks: Uint8Array[] = [];
      let received = 0;
      try {
      for await (const chunk of result.body) {
        signal.throwIfAborted();
        chunks.push(chunk);
        received += chunk.byteLength;
      }
      } catch (error) {
        throw new Error(`Stream ended with an error after ${received} bytes: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
      }
      signal.throwIfAborted();
      const bytes = Buffer.concat(chunks);
      if (!bytes.length) throw new Error('Nimi returned an empty audio artifact');
      return { base64: bytes.toString('base64'), mimeType: asset.mediaType || 'audio/wav' };
      });
    } finally {
      await stage('remove temporary copy', () => this.services.storage.assets.remove(asset.relativePath));
    }
  }

  private async synthesize(input: SpeechInput, signal: AbortSignal) {
    if (typeof input.text !== 'string' || !input.text.trim()) throw new Error('Speech text is required');
    let voiceId: string | undefined;
    if (input.reference || input.instruction?.trim()) {
      const configuration = await this.services.aiConfig.get();
      const key = createHash('sha256').update(JSON.stringify({ reference: input.reference, instruction: input.instruction,
        language: input.language, selections: configuration.effectiveSelections })).digest('hex');
      voiceId = input.reference ? this.voices.get(key) : undefined;
      if (!voiceId) {
        const spec: NimiLocalAppScenarioJobSpec = input.reference
          ? { type: 'voice-create', creationSource: 'reference-audio',
              referenceAudio: { type: 'bytes', bytes: this.audioBytes(input.reference) },
              referenceAudioMime: input.reference.mimeType, text: input.reference.text,
              languageHints: [], preferredName: 'VoiceStudio' }
          : { type: 'voice-create', creationSource: 'text-description', instructionText: input.instruction!,
              previewText: input.text, language: input.language || '', preferredName: 'VoiceStudio' };
        const created = await this.job(spec, signal);
        voiceId = created.voiceReference?.voiceAssetId;
        if (!voiceId || created.asset?.status !== 'active') throw new Error('Nimi voice creation returned no active voice');
        if (input.reference) this.voices.set(key, voiceId);
      }
    }
    const result = await this.job({ type: 'speech-synthesize', text: input.text,
      language: input.language || '', audioFormat: 'wav', emotion: '', timingMode: 'none',
      voiceRef: voiceId ? { type: 'voice-asset', id: voiceId } : null, voiceRenderHints: null,
    }, signal);
    const artifact = result.job.artifacts.find((item) => item.mimeType.startsWith('audio/'));
    if (!artifact) throw new Error('Nimi synthesis returned no audio artifact');
    try {
      return { ...await this.artifact(artifact.artifactId, signal),
        sampleRate: artifact.sampleRateHz, channels: artifact.channels, jobId: result.job.jobId };
    } catch (error) {
      throw new Error(`Receive audio from completed job ${result.job.jobId}, artifact ${artifact.artifactId}: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
    }
  }

  private async transcribe(input: AudioInput, signal: AbortSignal) {
    const result = await this.job({ type: 'speech-transcribe', mimeType: input.mimeType,
      audioSource: { type: 'bytes', bytes: this.audioBytes(input) }, language: input.language || '',
      timestamps: input.timestamps ?? false, prompt: '', responseFormat: '',
    }, signal);
    if (!result.job.transcription) throw new Error('Nimi returned no typed transcription');
    return result.job.transcription;
  }
}
