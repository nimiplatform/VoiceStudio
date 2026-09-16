import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: () => 'Something went wrong' }) }));
import { DictationErrorDetails } from './dictation-error-details';
afterEach(cleanup);

it('shows the actual Job rejection instead of only a failure heading', () => {
  render(<DictationErrorDetails detail="speech-transcribe job 01M2MS32QDMK76GY125Z7GVTYG: ai-output-invalid" />);
  expect(screen.getByRole('alert')).toHaveTextContent('01M2MS32QDMK76GY125Z7GVTYG: ai-output-invalid');
});
it('has a visible fallback when no diagnostic detail is available', () => {
  render(<DictationErrorDetails detail="  " />);
  expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong');
});
