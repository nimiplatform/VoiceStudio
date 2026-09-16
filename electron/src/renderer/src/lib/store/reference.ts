import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import { CLONE_MAX_SECONDS, REF_HARD_MAX_SECONDS } from '@/lib/api/generate';
import { probeAudioDuration } from '@/lib/audio/probe';
import { createObjectUrl, revokeObjectUrl } from '@/lib/audio/object-url';
import { patchCloneSettings } from './clone-settings';

export interface ReferenceState {
  sourceUri?: string;
  sourceMime?: string;
  pending?: boolean;
  file: File | null;
  durationSeconds: number | null;
  objectUrl: string | null;
}

export interface SetReferenceResult {
  /** False when the clip was rejected (longer than REF_HARD_MAX_SECONDS). */
  ok: boolean;
  durationSeconds: number | null;
  /** Longer than CLONE_MAX_SECONDS: accepted, but the UI should suggest trimming. */
  tooLong: boolean;
}

const EMPTY: ReferenceState = { file: null, durationSeconds: null, objectUrl: null };

export const recordingBusyStore = new Store(false);
export function setRecordingBusy(busy: boolean) {
  recordingBusyStore.setState(() => busy);
}

export const referenceStore = new Store<ReferenceState>(EMPTY);

// Monotonic token so a slow probe for an older pick can never overwrite a
// newer one (drop two files quickly, or clear while a probe is in flight).
let latestPick = 0;

export function useReference(): ReferenceState {
  return useStore(referenceStore);
}

function replaceState(next: ReferenceState): void {
  revokeObjectUrl(referenceStore.state.objectUrl);
  referenceStore.setState(() => next);
}

export function clearReference(): void {
  latestPick += 1;
  replaceState(EMPTY);
}

/** A URI is a usable source independently of an optional local preview copy. */
export function setReferenceUri(uri: string, mime: string): void {
  const url = new URL(uri.trim());
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.hash)
    throw new Error('Use a public HTTP(S) audio URL without credentials or fragments');
  latestPick += 1;
  patchCloneSettings({ selectedProfileId: null, refText: '', instruct: '', language: 'Auto', denoise: false });
  replaceState({ ...EMPTY, sourceUri: uri.trim(), sourceMime: mime });
}

/**
 * Set (or clear) the reference clip. Probes the duration first: clips over
 * REF_HARD_MAX_SECONDS are rejected outright (the engine cannot use them
 * without a transcript); clips over CLONE_MAX_SECONDS are accepted with
 * `tooLong` so the caller can nudge the user to trim. Picking a file
 * deselects any saved voice — exactly one of the two feeds `/generate`.
 */
export async function setReferenceFile(file: File | null, source?: { uri: string; mime: string }): Promise<SetReferenceResult> {
  const pick = ++latestPick;
  if (!file) {
    replaceState(EMPTY);
    return { ok: true, durationSeconds: null, tooLong: false };
  }
  referenceStore.setState((state) => ({ ...state, pending: true }));
  let durationSeconds: number | null;
  try {
    durationSeconds = await probeAudioDuration(file);
  } finally {
    if (pick === latestPick) referenceStore.setState(({ pending: _pending, ...state }) => state);
  }
  const superseded = pick !== latestPick;
  if (durationSeconds !== null && durationSeconds > REF_HARD_MAX_SECONDS) {
    return { ok: false, durationSeconds, tooLong: true };
  }
  const tooLong = durationSeconds !== null && durationSeconds > CLONE_MAX_SECONDS;
  if (!superseded) {
    // A new raw recording/upload is a new voice identity. Do not silently
    // carry the previous saved profile's transcript, language or delivery
    // direction into it.
    patchCloneSettings({
      selectedProfileId: null,
      refText: '',
      instruct: '',
      language: 'Auto',
      ...(source ? { denoise: false } : {}),
    });
    replaceState({ file, durationSeconds, objectUrl: createObjectUrl(file),
      ...(source ? { sourceUri: source.uri, sourceMime: source.mime } : {}) });
  }
  return { ok: true, durationSeconds, tooLong };
}
