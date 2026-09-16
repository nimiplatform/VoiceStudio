import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import type { GenerateResult } from '@/lib/api/types';
import { createObjectUrl, revokeObjectUrl } from '@/lib/audio/object-url';

export interface OutputState {
  result: GenerateResult | null;
  /** Object URL for `result.blob`, ready for `<audio src>` / waveform rendering. */
  objectUrl: string | null;
  /** The script that produced `result`. */
  text: string;
  details?: { mode: 'clone' | 'design'; instruct: string; language: string };
}

export const outputStore = new Store<OutputState>({ result: null, objectUrl: null, text: '' });

export function useLatestOutput(): OutputState {
  return useStore(outputStore);
}

export function setLatestOutput(result: GenerateResult, text: string, details?: OutputState['details']): void {
  revokeObjectUrl(outputStore.state.objectUrl);
  outputStore.setState(() => ({ result, objectUrl: createObjectUrl(result.blob), text, ...(details ? { details } : {}) }));
}

export function clearLatestOutput(): void {
  revokeObjectUrl(outputStore.state.objectUrl);
  outputStore.setState(() => ({ result: null, objectUrl: null, text: '' }));
}
