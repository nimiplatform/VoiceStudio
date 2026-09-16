import { useSyncExternalStore } from 'react';
import { LANGUAGES } from '@/lib/languages';

const STORAGE_KEY = 'voicestudio.dictation-settings.v1';
const listeners = new Set<() => void>();

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return;
    listeners.forEach((listener) => listener());
  });
}

function read(): { aecEnabled: boolean; inputLanguage: string } {
  const defaults = { aecEnabled: false, inputLanguage: 'Auto' };
  if (typeof localStorage === 'undefined') return defaults;
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return { aecEnabled: saved?.aecEnabled === true,
      inputLanguage: typeof saved?.inputLanguage === 'string' && LANGUAGES.includes(saved.inputLanguage)
        ? saved.inputLanguage : 'Auto' };
  } catch {
    return defaults;
  }
}

export function getAecEnabled(): boolean {
  return read().aecEnabled;
}

function update(patch: Partial<ReturnType<typeof read>>): void {
  if (typeof localStorage !== 'undefined') {
    // Read the shared record before writing so another window's latest choice
    // is preserved even before its storage event reaches this window.
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...read(), ...patch }));
  }
  listeners.forEach((listener) => listener());
}

export function setAecEnabled(value: boolean): void {
  update({ aecEnabled: value });
}

export function getDictationInputLanguage(): string {
  return read().inputLanguage;
}

export function setDictationInputLanguage(inputLanguage: string): void {
  if (!LANGUAGES.includes(inputLanguage)) throw new Error('Unknown input language');
  update({ inputLanguage });
}

export function useDictationInputLanguage(): string {
  return useSyncExternalStore(subscribe, getDictationInputLanguage, () => 'Auto');
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAecEnabled(): boolean {
  return useSyncExternalStore(subscribe, getAecEnabled, () => false);
}
