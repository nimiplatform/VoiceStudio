import { createNimiClient, type NimiLocalAppClient } from '@nimiplatform/sdk';
import { createNimiLocalAppStandardShellSurface } from '@nimiplatform/kit/shell/renderer/bridge';

// Same-package reference: src/shell/auth/local-app-client.ts.
let client: NimiLocalAppClient | undefined;
export function getNimiClient(): NimiLocalAppClient {
  return client ??= createNimiClient({
    localApp: { standardShell: createNimiLocalAppStandardShellSurface() },
  });
}
export const NIMI_APP_ID = 'nimiplatform.voicestudio';
export const NIMI_CAPABILITIES = [
  'audio.synthesize', 'audio.transcribe', 'voice.create', 'text.generate', 'audio.separate',
] as const;
