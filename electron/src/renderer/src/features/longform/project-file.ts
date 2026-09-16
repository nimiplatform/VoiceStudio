import type { Draft, Mode } from './longform-session';

export function encodeProjectFile(name: string, mode: Mode, draft: Draft): string {
  return JSON.stringify({ format: 'voicestudio.longform-project', version: 1, name, mode, draft }, null, 2);
}

export function decodeProjectFile(text: string): { name: string; mode: Mode; draft: Draft } {
  const file = JSON.parse(text);
  const draft = file?.draft;
  if (file?.format !== 'voicestudio.longform-project' || file.version !== 1
    || !['stories', 'audiobook'].includes(file.mode) || typeof file.name !== 'string' || !file.name.trim()
    || !draft || typeof draft.script !== 'string' || typeof draft.title !== 'string'
    || !Array.isArray(draft.lines) || !Array.isArray(draft.cast)
    || draft.lines.some((line: unknown) => !line || typeof line !== 'object' || !('text' in line) || typeof line.text !== 'string' || !('id' in line) || typeof line.id !== 'string')
    || draft.cast.some((cast: unknown) => !cast || typeof cast !== 'object' || !('name' in cast) || typeof cast.name !== 'string' || !('id' in cast) || typeof cast.id !== 'string')) {
    throw new Error('Invalid VoiceStudio project file');
  }
  return { name: file.name, mode: file.mode, draft };
}
