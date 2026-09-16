import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock('@/hooks/use-profiles', () => ({
  useCreateCloneProfile: () => ({ mutateAsync: mock.save, isPending: false }),
  useProfiles: () => ({ data: [] }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="#models">{children}</a>,
}));
vi.mock('./portrait-search', () => ({ PortraitSearch: () => null }));
vi.mock('./profile-image-editor', () => ({ ProfileImageEditor: () => null }));
vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => null }));
import { OptionalDetails, SaveProfileForm } from './reference-panel';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('blocks direct submission while transcribing and permits saving afterward', async () => {
  const file = new File(['audio'], 'voice.wav', { type: 'audio/wav' });
  const done = vi.fn();
  mock.save.mockResolvedValue({ id: 'saved', name: 'Voice' });
  const { container, rerender } = render(
    <SaveProfileForm file={file} onDone={done} transcribing selectOnSave={false} />,
  );
  fireEvent.change(screen.getByLabelText('clone.profile_name'), { target: { value: 'Voice' } });
  await act(async () => {
    fireEvent.submit(container.querySelector('form')!);
  });
  expect(mock.save).not.toHaveBeenCalled();
  expect(done).not.toHaveBeenCalled();
  rerender(<SaveProfileForm file={file} onDone={done} transcribing={false} selectOnSave={false} />);
  fireEvent.submit(container.querySelector('form')!);
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(mock.save).toHaveBeenCalledWith(
    expect.objectContaining({ name: 'Voice', refAudio: file }),
  );
});

it('opens optional details and explains a failed automatic transcription', async () => {
  const retry = vi.fn();
  render(<OptionalDetails transcription={{ state: 'failed', retry }} />);

  expect(await screen.findByText('transcriptions.failed')).toHaveClass('text-destructive');
  fireEvent.click(screen.getByRole('button', { name: 'referenceAsr.retry' }));
  expect(retry).toHaveBeenCalledOnce();
});

it('opens formal Nimi configuration for missing reference ASR and preserves retry', () => {
  const retry = vi.fn();
  render(<OptionalDetails transcription={{ state: 'unavailable', retry }} />);
  fireEvent.click(screen.getByRole('button', { name: 'nimi.configuration' }));
  expect(window.location.hash).toBe('#/settings/models');
  fireEvent.click(screen.getByRole('button', { name: 'referenceAsr.retry' }));
  expect(retry).toHaveBeenCalledOnce();
});
