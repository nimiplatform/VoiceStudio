import { apiFetch, apiJson } from './client';
import type { Profile } from './types';

export interface CreateCloneProfileInput {
  name: string;
  seed?: number | null;
  image?: File;
  refAudio?: File | Blob | null;
  refAudioName?: string;
  refAudioUri?: string;
  refAudioMime?: string;
  historyId?: string;
  refText?: string;
  instruct?: string;
  language?: string;
}

export async function listProfiles(): Promise<Profile[]> {
  return apiJson<Profile[]>('/profiles');
}

export async function updateProfileImage(id: string, image: File): Promise<Profile> {
  const form = new FormData();
  form.append('image', image);
  return apiJson<Profile>(`/profiles/${encodeURIComponent(id)}/image`, {
    method: 'PUT',
    body: form,
  });
}

export async function createCloneProfile(input: CreateCloneProfileInput): Promise<Profile> {
  const form = new FormData();
  form.append('name', input.name);
  if (input.image) form.append('image', input.image);
  const fileName =
    input.refAudioName ||
    (input.refAudio instanceof File ? input.refAudio.name : '') ||
    'profile.wav';
  if (input.refAudio) form.append('ref_audio', input.refAudio, fileName);
  form.append('ref_text', input.refText ?? '');
  if (input.refAudioUri) {
    form.append('ref_audio_uri', input.refAudioUri);
    form.append('ref_audio_mime', input.refAudioMime || '');
  }
  if (input.historyId) form.append('history_id', input.historyId);
  form.append('instruct', input.instruct?.trim() ?? '');
  form.append('language', input.language || 'Auto');
  form.append('kind', 'clone');
  if (input.seed != null) form.append('seed', String(input.seed));
  return apiJson<Profile>('/profiles', { method: 'POST', body: form });
}

export async function deleteProfile(id: string): Promise<void> {
  await apiFetch(`/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
