import { protocol, session, webContents, ipcMain } from 'electron';
import {
  registerNimiElectronAppBridge,
  type RegisteredNimiElectronAppBridge,
} from '@nimiplatform/kit/shell/electron/main';

// Foundation copied from this combination's generated reference Host.
export const NIMI_APP_ID = 'nimiplatform.voicestudio';
declare const __NIMI_ELECTRON_PRODUCTION__: boolean;

export function readDevelopmentRendererUrl(): string {
  const flag = '--nimi-dev-renderer-url';
  const prefix = `${flag}=`;
  const args = process.argv.filter((value) => value === flag || value.startsWith(prefix));
  if (!args.length) return '';
  if (typeof __NIMI_ELECTRON_PRODUCTION__ !== 'undefined' && __NIMI_ELECTRON_PRODUCTION__)
    throw new Error('The production Electron bundle rejects --nimi-dev-renderer-url.');
  if (args.length !== 1 || args[0] === flag)
    throw new Error('Nimi development renderer URL must be singular.');
  const parsed = new URL(args[0].slice(prefix.length));
  if (parsed.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]', '::1'].includes(parsed.hostname)
    || !parsed.port || parsed.username || parsed.password || parsed.pathname !== '/'
    || parsed.search || parsed.hash)
    throw new Error('Nimi development renderer URL must be exact loopback.');
  return parsed.origin;
}


export function registerNimiHost(rendererUrl: string, onSessionInvalidated: () => void,
  onSessionReady: (services: RegisteredNimiElectronAppBridge['services']) => void) {
  return registerNimiElectronAppBridge({
    appId: NIMI_APP_ID,
    allowedRendererUrls: [rendererUrl],
    assetMediaPlatform: { protocol, webRequest: session.defaultSession.webRequest, webContents },
    ipcMain,
    onSessionInvalidated,
    onSessionReady,
  });
}
