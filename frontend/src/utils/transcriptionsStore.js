/**
 * Single source of truth for the dictation-history localStorage store (#23).
 * Relocates the key/event consts + the reader that were duplicated across
 * Transcriptions.jsx and Projects.jsx. No version/rename/rewrite — the storage
 * contract (key, entry shape, 200-cap) is unchanged; this only de-dups the read.
 */
export const TRANSCRIPTIONS_KEY = 'omni_transcriptions';
export const TRANSCRIPTION_EVENT = 'omni:transcription-added';

/**
 * Read the transcription history. Never throws; always returns an array
 * (newest-first, as written). [] on absent/empty/malformed/non-array/blocked.
 * @returns {Array<object>}
 */
export function loadTranscriptions() {
  try {
    const parsed = JSON.parse(localStorage.getItem(TRANSCRIPTIONS_KEY) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** Append a completed transcript using the existing 200-entry storage contract. */
export function addTranscription(entry) {
  const newEntry = {
    id: Date.now(),
    text: entry.text || '',
    language: entry.language || 'unknown',
    ...(entry.input_language ? { input_language: entry.input_language } : {}),
    duration_s: entry.duration_s || 0,
    segments: entry.segments || [],
    timestamp: new Date().toISOString(),
    ...(typeof entry.refined_text === 'string' && entry.refined_text
      ? { refined_text: entry.refined_text }
      : {}),
  };
  const list = [newEntry, ...loadTranscriptions()].slice(0, 200);
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(list));
  window.dispatchEvent(new CustomEvent(TRANSCRIPTION_EVENT, { detail: newEntry }));
  return newEntry;
}

/** Observe writes from this document and sibling desktop windows. */
export function subscribeTranscriptions(listener) {
  const refresh = () => listener(loadTranscriptions());
  const storage = (event) => {
    if (event.key === TRANSCRIPTIONS_KEY) refresh();
  };
  window.addEventListener(TRANSCRIPTION_EVENT, refresh);
  window.addEventListener('storage', storage);
  return () => {
    window.removeEventListener(TRANSCRIPTION_EVENT, refresh);
    window.removeEventListener('storage', storage);
  };
}
