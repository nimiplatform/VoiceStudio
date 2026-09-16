import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@tanstack/react-router', async (original) => ({
  ...await original<object>(),
  useNavigate: () => vi.fn(),
  useRouterState: () => '/settings/general',
  Link: ({ to, children, className }: { to: string; children: ReactNode; className?: string }) => <a href={to} className={className}>{children}</a>,
}));
vi.mock('react-i18next', async (original) => ({ ...await original<object>(), useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', async (original) => ({ ...await original<object>(), apiJson: mocks.api }));

import { SettingsPage } from './settings-page';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('exposes cleanup in General settings and preserves an explicit off choice on reopening', async () => {
  let config = { auto: true, smart_cleanup: true, self_correction: true, preserve_technical: true, llm_ready: true };
  mocks.api.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path !== '/api/settings/dictation-refinement') throw new Error('Unexpected settings request: ' + path);
    if (options?.method === 'PUT') config = { ...config, ...JSON.parse(String(options.body)) };
    return { ...config };
  });
  const open = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><SettingsPage /></QueryClientProvider>);
  const first = open();
  const toggle = await screen.findByRole('switch', { name: 'settings.llmskills_dictation_refinement_name' });
  expect(toggle).toBeChecked();
  expect(screen.getByText(/transcriptions.refinement_data_flow/)).toBeInTheDocument();
  expect(mocks.api.mock.calls.every(([, options]) => options?.method !== 'PUT')).toBe(true);
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  expect(mocks.api).toHaveBeenCalledWith('/api/settings/dictation-refinement', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ auto: false }) }));
  first.unmount();
  open();
  expect(await screen.findByRole('switch', { name: 'settings.llmskills_dictation_refinement_name' })).not.toBeChecked();
});
