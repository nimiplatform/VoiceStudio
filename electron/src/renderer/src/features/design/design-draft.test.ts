import { beforeEach, describe, expect, it } from 'vitest';
import type { Profile } from '@/lib/api/types';
import { readDraft, restoreDesignProfile, STORAGE, editDesignAttributes, designInstruction } from './design-draft';

const profile = {
  id: 'designed-voice',
  name: 'Narrator',
  kind: 'design',
  ref_audio_path: 'preview.wav',
  ref_text: null,
  instruct: 'female, low pitch',
  language: 'French',
  seed: 42,
  personality: null,
  vd_states: JSON.stringify({ Gender: 'male' }),
  created_at: 1,
  is_locked: false,
} satisfies Profile;

describe('designed voice drafts', () => {
  it('keeps a free description when the old trait mapper recognizes nothing', () => {
    const description = 'A calm adult English narrator with a warm, clear voice.';
    expect(designInstruction({ Gender: 'Auto' }, description)).toBe(description);
    expect(designInstruction({ Pitch: 'low pitch' }, description)).toBe(
      `${description}\nVoice traits (take priority): low pitch.`,
    );
    expect(designInstruction({}, '  ')).toBe('');
  });
  beforeEach(() => localStorage.clear());
  it('keeps an unchanged saved voice but detaches identity when its design changes', () => {
    const draft = { ...readDraft(), ...restoreDesignProfile(profile, 9), text: 'Keep my script.' };
    expect(editDesignAttributes(draft, { ...draft.attrs })).toBe(draft);
    expect(editDesignAttributes(draft, { ...draft.attrs, Pitch: 'high pitch' })).toMatchObject({
      profileId: null, text: 'Keep my script.', attrs: { Pitch: 'high pitch' },
    });
    expect(draft.profileId).toBe(profile.id);
  });

  it('persists the selected profile identity with the draft', () => {
    localStorage.setItem(
      STORAGE,
      JSON.stringify({ text: 'Hello', description: 'A warm, clear voice.', attrs: { Gender: 'female' }, seed: 7, profileId: 'voice' }),
    );
    expect(readDraft()).toMatchObject({ text: 'Hello', description: 'A warm, clear voice.', seed: 7, profileId: 'voice' });
  });

  it('restores profile language, seed and a complete recipe without stale values', () => {
    const restored = restoreDesignProfile(profile, 9);
    expect(restored).toMatchObject({
      profileId: 'designed-voice',
      language: 'French',
      seed: 42,
      attrs: { Gender: 'male', Pitch: 'low pitch', Age: 'Auto' },
    });

    expect(
      restoreDesignProfile({ ...profile, vd_states: '{broken', seed: null, language: null }, 9),
    ).toMatchObject({
      language: 'Auto',
      seed: 9,
      attrs: { Gender: 'female', Pitch: 'low pitch' },
    });
  });
});
