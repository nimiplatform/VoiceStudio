import { describe, expect, it } from 'vitest';
import { cloneBlocker } from './clone-readiness';
import type { Profile } from './api/types';
const ready = { text: 'Hello', profileId: null, profiles: [], fileSize: 10 };
describe('clone readiness', () => {
  it('requires nonempty text and a real reference', () => {
    expect(cloneBlocker({ ...ready, fileSize: 0 })).toBe('reference');
    expect(cloneBlocker({ ...ready, text: ' \n ' })).toBe('text');
    expect(cloneBlocker(ready)).toBeNull();
  });
  it('accepts a URI source without pretending a local file exists', () => {
    expect(cloneBlocker({ ...ready, fileSize: 0, sourceUri: 'https://example.com/voice.wav' })).toBeNull();
    expect(cloneBlocker({ ...ready, fileSize: 0, profileId: 'uri-voice', profiles: [
      { id: 'uri-voice', kind: 'clone', ref_audio_path: null, ref_audio_uri: 'https://example.com/voice.wav' } as Profile,
    ] })).toBeNull();
  });
  it('rejects missing saved profiles and profiles without reference audio', () => {
    expect(cloneBlocker({ ...ready, profileId: 'deleted' })).toBe('reference');
    expect(
      cloneBlocker({
        ...ready,
        profileId: 'voice',
        profiles: [{ id: 'voice', kind: 'clone', ref_audio_path: null } as Profile],
      }),
    ).toBe('reference');
    expect(
      cloneBlocker({
        ...ready,
        profileId: 'voice',
        fileSize: 0,
        profiles: [{ id: 'voice', kind: 'clone', ref_audio_path: 'voice.wav' } as Profile],
      }),
    ).toBeNull();
  });
  it('waits for saved voices to load and recordings or files to finish processing', () => {
    expect(cloneBlocker({ ...ready, profileId: 'voice', profiles: undefined })).toBe('loading');
    expect(cloneBlocker({ ...ready, busy: true })).toBe('preparing');
  });
});
