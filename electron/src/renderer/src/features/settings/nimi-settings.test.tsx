import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ ready: false, allow: true, get: vi.fn(), status: vi.fn() }));
vi.mock('@/lib/nimi-client', () => ({
  NIMI_APP_ID: 'test', NIMI_CAPABILITIES: ['text.generate'],
  getNimiClient: () => ({ auth: { status: state.status }, aiConfig: { get: state.get } }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@nimiplatform/kit/features/model-config', () => ({
  ModelConfigAIConfigSurface: ({ revision, loadError, onRetry }: { revision?: number; loadError?: string; onRetry: () => void }) =>
    <div><span>{revision === undefined ? 'No snapshot' : `Revision ${revision}`}</span><span>{loadError}</span><button onClick={onRetry}>Retry</button></div>,
}));
import { NimiSettings } from './nimi-settings';

it('rebinds before reading configuration and clears a stale snapshot when access is refused', async () => {
  state.ready = false;
  state.allow = true;
  state.status.mockImplementation(async () => {
    state.ready = state.allow;
    return { sessionBound: state.ready, reasonCode: 'session-invalid' };
  });
  state.get.mockImplementation(async () => {
    if (!state.ready) throw new Error('runtime-unauthenticated');
    return { revision: 7, config: { capabilities: {} }, effectiveSelections: [] };
  });
  render(<NimiSettings />);
  await waitFor(() => expect(screen.getByText('Revision 7')).toBeInTheDocument());
  state.allow = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.getByText('session-invalid')).toBeInTheDocument());
  expect(screen.getByText('No snapshot')).toBeInTheDocument();
  expect(state.get).toHaveBeenCalledTimes(1);
  state.allow = true;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.getByText('Revision 7')).toBeInTheDocument());
  expect(state.status).toHaveBeenCalledTimes(3);
});
