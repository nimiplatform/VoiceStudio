import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import '@/i18n';
import { ScriptPanel } from './script-panel';
const setCloneSetting = vi.fn();
vi.mock('@/lib/store/clone-settings', () => ({
  useCloneSetting: () => 'hello [laughter] world',
  setCloneSetting: (...args: unknown[]) => setCloneSetting(...args),
}));
describe('ScriptPanel', () => {
  it('preserves legacy text for editing without offering unsupported expression insertion', () => {
    render(<ScriptPanel />);
    const textarea = screen.getByRole('textbox', { name: 'Script' });
    expect(textarea).toHaveValue('hello [laughter] world');
    expect(screen.queryByRole('button', { name: 'Insert expression token' })).toBeNull();
    fireEvent.click(textarea);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
