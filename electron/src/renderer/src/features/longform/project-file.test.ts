import { expect, it } from 'vitest';
import { decodeProjectFile, encodeProjectFile } from './project-file';
import { blankLongformDraft } from './longform-session';

it('retains source, cast, voice references and output when transferred between profiles', () => {
  const draft = { ...blankLongformDraft(), title: 'Two voices', script: '# Opening\nA story.',
    lines: [{ id: 'line', text: 'A story.', profileId: 'voice-a' }],
    cast: [{ id: 'narrator', name: 'Narrator', profileId: 'voice-b' }], output: 'story.mp3' };
  const restored = decodeProjectFile(encodeProjectFile('Saved story', 'stories', draft));
  expect(restored.draft).toEqual(draft);
  expect(restored.name).toBe('Saved story');
});

it('rejects unrelated files before they replace an existing draft', () => {
  expect(() => decodeProjectFile('{"name":"wrong"}')).toThrow('Invalid VoiceStudio');
});
