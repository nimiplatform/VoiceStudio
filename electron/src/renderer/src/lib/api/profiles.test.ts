import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ json: vi.fn() }));
vi.mock('./client', () => ({ apiJson: mock.json, apiFetch: vi.fn() }));
import { createCloneProfile, updateProfileImage } from './profiles';
afterEach(() => vi.clearAllMocks());
it('preserves take seed zero and full transcript and the complete voice description', async () => {
  mock.json.mockResolvedValue({});
  const text =
    'A complete transcript longer than fifty characters must remain aligned with the full audio.';
  await createCloneProfile({
    name: 'Saved take',
    refAudio: new Blob(['audio']),
    refText: text,
    seed: 0,
    language: 'French',
    instruct: 'A warm female narrator with gentle delivery.',
  });
  const form = mock.json.mock.calls[0][1].body as FormData;
  expect(form.get('seed')).toBe('0');
  expect(form.get('ref_text')).toBe(text);
  expect(form.get('language')).toBe('French');
  expect(form.get('instruct')).toBe('A warm female narrator with gentle delivery.');
});
it('does not invent a seed when saving an ordinary recording', async () => {
  mock.json.mockResolvedValue({});
  await createCloneProfile({ name: 'Recording', refAudio: new Blob(['audio']) });
  expect((mock.json.mock.calls[0][1].body as FormData).has('seed')).toBe(false);
});

it('updates profile images with the supported method and an encoded identifier', async () => {
  mock.json.mockResolvedValue({ id: 'voice/1' });
  const image = new File(['portrait'], 'portrait.png', { type: 'image/png' });

  await updateProfileImage('voice/1', image);

  expect(mock.json).toHaveBeenCalledWith(
    '/profiles/voice%2F1/image',
    expect.objectContaining({ method: 'PUT' }),
  );
  expect((mock.json.mock.calls[0][1].body as FormData).get('image')).toBe(image);
});
