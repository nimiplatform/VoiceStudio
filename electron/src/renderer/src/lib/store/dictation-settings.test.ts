import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { getAecEnabled, getDictationInputLanguage, setAecEnabled, setDictationInputLanguage, useDictationInputLanguage } from './dictation-settings';

const key = 'voicestudio.dictation-settings.v1';
beforeEach(() => localStorage.clear());
afterEach(cleanup);

it('preserves both settings when another window has changed the shared record', () => {
  setAecEnabled(true);
  setDictationInputLanguage('English');
  expect(getAecEnabled()).toBe(true);
  localStorage.setItem(key, JSON.stringify({ aecEnabled: true, inputLanguage: 'Chinese' }));
  setAecEnabled(false);
  expect(getDictationInputLanguage()).toBe('Chinese');
  setDictationInputLanguage('English');
  expect(JSON.parse(localStorage.getItem(key)!)).toEqual({ aecEnabled: false, inputLanguage: 'English' });
});

it('updates the page from a shared-window storage event', () => {
  const { result } = renderHook(useDictationInputLanguage);
  expect(result.current).toBe('Auto');
  act(() => setDictationInputLanguage('English'));
  expect(result.current).toBe('English');
  act(() => {
    localStorage.setItem(key, JSON.stringify({ inputLanguage: 'Chinese' }));
    window.dispatchEvent(new StorageEvent('storage', { key }));
  });
  expect(result.current).toBe('Chinese');
});

it('defaults unknown stored language to Auto without discarding AEC', () => {
  localStorage.setItem(key, JSON.stringify({ aecEnabled: true, inputLanguage: 'not-a-language' }));
  expect(getDictationInputLanguage()).toBe('Auto');
  expect(getAecEnabled()).toBe(true);
});
