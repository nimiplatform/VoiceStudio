import type { Profile } from '@/lib/api/types';
export type CloneBlocker = 'engine' | 'reference' | 'text' | 'loading' | 'preparing' | null;
export function cloneBlocker({
  text,
  profileId,
  profiles,
  fileSize,
  sourceUri,
  busy = false,
}: {
  text: string;
  profileId: string | null;
  profiles: Profile[] | undefined;
  fileSize: number;
  sourceUri?: string;
  busy?: boolean;
}): CloneBlocker {
  if (busy) return 'preparing';
  if (profileId) {
    if (!profiles) return 'loading';
    if (
      !profiles.some(
        (profile) =>
          profile.id === profileId && profile.kind === 'clone' && Boolean(profile.ref_audio_path || profile.ref_audio_uri || profile.nimi_voice_asset_id),
      )
    )
      return 'reference';
  } else if (fileSize <= 0 && !sourceUri) return 'reference';
  if (!text.trim()) return 'text';
  return null;
}
