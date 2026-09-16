import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ api: vi.fn(), save: vi.fn() }));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => ({ data: [] }) }));
vi.mock('@/hooks/use-history', () => ({ useHistory: () => ({ data: [] }) }));
vi.mock('../longform/project-library', () => ({ projectLibrary: { list: async () => [] } }));
vi.mock('../longform/longform-session', () => ({ useLongformSession: () => ({ active: null }) }));
vi.mock('../dub/dub-session', () => ({ useDubSession: () => ({ phase: 'idle', filename: '', jobId: null }) }));
vi.mock('@/components/bridge', () => ({ getBridge: () => ({ files: { saveAudio: mocks.save } }) }));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.api,
  apiPath: (path: string) => '/api' + path,
  describeError: (error: Error) => error.message,
}));
vi.mock('@/components/audio-preview-button', () => ({ AudioPreviewButton: () => <button>preview</button> }));
vi.mock('@/components/workspace-sidebar', () => ({ SecondarySidebar: ({ children }: { children: ReactNode }) => <aside>{children}</aside> }));
vi.mock('@/components/app-shell/workspace-header', () => ({ WorkspaceHeader: ({ children }: { children: ReactNode }) => <header>{children}</header> }));

import { ProjectsPage } from './projects-page';

beforeEach(() => {
  localStorage.clear();
  mocks.save.mockReset(); mocks.api.mockReset();
  mocks.api.mockImplementation(async (path: string) => {
    if (path === '/longform/jobs') return { jobs: [
      { job_id: 'finished-book', title: 'Finished book', output: 'audiobook_finished-book.m4b', type: 'audiobook' },
      { job_id: 'finished-story', title: 'Finished story', output: 'story_finished-story.mp3', type: 'story' },
    ] };
    if (['/projects', '/export/history', '/export/record'].includes(path)) return [];
    throw new Error('Unexpected operation: ' + path);
  });
});
afterEach(cleanup);

async function resultRow(title: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ProjectsPage /></QueryClientProvider>);
  return within((await screen.findByRole('heading', { name: title })).closest('article')!);
}

it.each([
  ['Finished book', 'audiobook_finished-book.m4b'],
  ['Finished story', 'story_finished-story.mp3'],
])('exports the existing %s in its original format without synthesis', async (title, filename) => {
  mocks.save.mockResolvedValue({ canceled: false, path: 'D:/exports/' + filename });
  const row = await resultRow(title);
  fireEvent.click(row.getByRole('button', { name: 'audiobook.download' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith({ url: '/api/audio/' + filename, suggestedName: filename }));
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/export/record', expect.objectContaining({ method: 'POST' })));
  expect(mocks.api.mock.calls.every(([path]) => ['/projects', '/longform/jobs', '/export/history', '/export/record'].includes(path))).toBe(true);
});

it('blocks duplicate saves while the dialog is pending and records nothing on cancel', async () => {
  let finish!: (result: { canceled: boolean }) => void;
  mocks.save.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const row = await resultRow('Finished book');
  const button = row.getByRole('button', { name: 'audiobook.download' });
  fireEvent.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  fireEvent.click(button);
  expect(mocks.save).toHaveBeenCalledTimes(1);
  finish({ canceled: true });
  await waitFor(() => expect(button).toBeEnabled());
  expect(mocks.api.mock.calls.some(([path]) => path === '/export/record')).toBe(false);
});
