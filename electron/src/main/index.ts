import { installWatchFolders } from './watch-folders';
import { installNativeCapture } from './native-capture';
import { isTrustedRenderer } from './trusted-renderer';
import { dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { app, BrowserWindow, ipcMain, session, shell, Tray } from 'electron';
import { configureNimiElectronAppHostProfile } from '@nimiplatform/kit/shell/electron/host-profile';
import { electronApp, is, optimizer } from '@electron-toolkit/utils';
import { BackendSupervisor, backendRoot } from './business-backend';
import { NimiJobs } from './nimi-jobs';
import { registerIpc, wireWindowMaximizeEvents } from './ipc';
import { startDevBackendProxy } from './dev-backend-proxy';
import { installRendererPermissions } from './media-permissions';
import { installBlankWindowGuard } from './blank-window-guard';
import { shouldOpenDevTools } from './devtools-policy';
import {
  installMainProcessErrorHandlers,
  MainErrorJournal,
  observeMainProcessTask,
} from './main-error-journal';
import { activateLiveWindow, isLiveWindow } from './window-safety';

try {
  configureNimiElectronAppHostProfile(app);
} catch (error) {
  process.stderr.write(`[nimi-app-host-profile] ${error instanceof Error ? error.message : String(error)}\n`);
  app.exit(78);
  throw error;
}
const { registerNimiHost, readDevelopmentRendererUrl } = await import('./nimi-host');
const { APP_ORIGIN, installAppProtocol, registerAppScheme } = await import('./protocol');

// Packaged GUI launches can inherit a short-lived terminal pipe. When that
// launcher exits, diagnostic console writes emit EPIPE asynchronously and can
// otherwise take down the healthy Electron main process. In-app log rings keep
// the same backend events, so a closed stdout/stderr sink is safe to ignore.
process.stdout?.on('error', () => {});
process.stderr?.on('error', () => {});

const APP_USER_MODEL_ID = 'com.voicestudio.desktop';
app.setName('VoiceStudio');
const here = dirname(fileURLToPath(import.meta.url));
const nativeAppIconPath = join(here, '..', 'assets', 'native', process.platform === 'win32' ? 'windows.ico' : process.platform === 'darwin' ? 'macos.png' : 'app.png');
const PRELOAD_PATH = join(here, 'preload.cjs');
const RENDERER_DIR = join(here, '../dist');
const supervisedRenderer = readDevelopmentRendererUrl();
if (supervisedRenderer) Object.assign(process.env, { ELECTRON_RENDERER_URL: supervisedRenderer });

// Neutral startup surface; transparent caption controls follow the CSS title bar.
const BACKGROUND = '#0a0a0a';
const OVERLAY = { color: '#00000000', symbolColor: '#737373', height: 52 };

registerAppScheme();

const mainErrors = new MainErrorJournal(
  join(app.getPath('userData'), 'main-process-errors.json'),
  app.getVersion(),
);
installMainProcessErrorHandlers(mainErrors);

let mainWindow: BrowserWindow | null = null;
let closeWatch: (() => void) | null = null;
let supervisor: BackendSupervisor | null = null;
let quitting = false;
let tray: Tray | null = null;
let closeCapture: (() => void) | null = null;
let closeDevProxy: (() => Promise<void>) | null = null;
let quitPreparation: Promise<void> | null = null;

const PERSISTENCE_FLUSH_REQUEST = 'app:flush-persistence';
const PERSISTENCE_FLUSHED = 'app:persistence-flushed';

async function flushRendererPersistence(): Promise<void> {
  const owner = mainWindow;
  if (!owner || owner.isDestroyed() || owner.webContents.isDestroyed()) return;
  const token = randomUUID();
  await new Promise<void>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      ipcMain.removeListener(PERSISTENCE_FLUSHED, acknowledge);
      resolve();
    };
    const acknowledge = (event: Electron.IpcMainEvent, received: unknown) => {
      if (event.sender === owner.webContents && received === token) finish();
    };
    const timeout = setTimeout(finish, 2500);
    ipcMain.on(PERSISTENCE_FLUSHED, acknowledge);
    try {
      owner.webContents.send(PERSISTENCE_FLUSH_REQUEST, token);
    } catch {
      finish();
    }
  });
}

async function tearDownNativeResources(): Promise<void> {
  closeWatch?.();
  closeWatch = null;
  closeCapture?.();
  closeCapture = null;
  tray?.destroy();
  tray = null;
  const proxyTeardown = closeDevProxy?.() ?? Promise.resolve();
  closeDevProxy = null;
  await Promise.allSettled([supervisor?.shutdown() ?? Promise.resolve(), proxyTeardown]);
}

function beginOrderlyQuit(flushPersistence = true): Promise<void> {
  if (quitPreparation) return quitPreparation;
  quitPreparation = (async () => {
    if (flushPersistence) await flushRendererPersistence();
    const teardown = tearDownNativeResources();
    const timeout = new Promise<void>((resolve) => setTimeout(resolve, 5000));
    await Promise.race([teardown, timeout]);
    quitting = true;
    app.quit();
  })();
  return quitPreparation;
}

function brandIconPath(file: string): string {
  return app.isPackaged
    ? join(process.resourcesPath, 'brand', file)
    : join(backendRoot(), 'frontend', 'src-tauri', 'icons', file);
}

function createWindow(): BrowserWindow {
  const appUrl =
    is.dev && process.env.ELECTRON_RENDERER_URL
      ? process.env.ELECTRON_RENDERER_URL
      : `${APP_ORIGIN}/index.html`;
  console.info('[renderer] loading', appUrl);
  const win = new BrowserWindow({
    icon: nativeAppIconPath,
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    show: false,
    backgroundColor: BACKGROUND,
    titleBarStyle: 'hidden',
    ...(process.platform === 'darwin'
      ? { trafficLightPosition: { x: 12, y: 10 } }
      : { titleBarOverlay: OVERLAY }),
    webPreferences: {
      preload: PRELOAD_PATH,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });

  win.on('ready-to-show', () => activateLiveWindow(win));
  win.webContents.on('did-finish-load', () => console.info('[renderer] loaded', win.webContents.getURL()));
  win.webContents.on('did-fail-load', (_event, code, description, url) => console.error('[renderer] failed', code, description, url));
  win.on('close', (event) => {
    if (quitting) return;
    event.preventDefault();
    if (process.platform === 'darwin') {
      observeMainProcessTask(
        flushRendererPersistence().finally(() => {
          if (!win.isDestroyed()) win.destroy();
        }),
        mainErrors,
        'Window persistence flush',
      );
      return;
    }
    observeMainProcessTask(beginOrderlyQuit(), mainErrors, 'Orderly quit');
  });
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
    if (process.platform !== 'darwin' && !quitting) app.quit();
  });
  wireWindowMaximizeEvents(win);
  installBlankWindowGuard(
    win,
    appUrl,
    app.getLocale(),
    process.env.ELECTRON_RENDERER_URL,
    async () => {
      await win.webContents.session.clearCache();
      app.relaunch();
      // The independent fallback has no mounted persistence handler to flush.
      await beginOrderlyQuit(false);
    },
  );

  win.webContents.setWindowOpenHandler(({ url }) => {
    void openOutside(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (isOwnUrl(url)) return;
    event.preventDefault();
    void openOutside(url);
  });

  if (is.dev && process.env.ELECTRON_RENDERER_URL) {
    // The blank-window guard owns bounded renderer recovery; consume the load
    // promise so a transient dev-server/package read failure is not uncaught.
    void win.loadURL(appUrl).catch(() => {});
    if (shouldOpenDevTools()) win.webContents.openDevTools({ mode: 'detach' });
  } else {
    void win.loadURL(appUrl).catch(() => {});
  }
  return win;
}

function isOwnUrl(url: string): boolean {
  const dev = process.env.ELECTRON_RENDERER_URL;
  return isTrustedRenderer(url, dev);
}

async function openOutside(url: string): Promise<void> {
  try {
    const parsed = new URL(url);
    if (
      parsed.protocol === 'http:' ||
      parsed.protocol === 'https:' ||
      parsed.protocol === 'mailto:'
    ) {
      await shell.openExternal(parsed.toString());
    }
  } catch {
    /* unparsable URLs are simply dropped */
  }
}

if (process.env.VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES !== '1' && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    activateLiveWindow(mainWindow);
  });

  void app
    .whenReady()
    .then(async () => {
      electronApp.setAppUserModelId(APP_USER_MODEL_ID);
      installRendererPermissions(session.defaultSession, process.env.ELECTRON_RENDERER_URL);
      app.on('browser-window-created', (_event, window) => optimizer.watchWindowShortcuts(window));

      let jobs: NimiJobs | undefined;
      const nimi = registerNimiHost(supervisedRenderer || APP_ORIGIN + '/index.html',
        () => { jobs?.invalidate(); supervisor?.cancelTasks(); },
        services => { jobs = new NimiJobs(services); });
      jobs ??= new NimiJobs(nimi.services);
      const backend = new BackendSupervisor({ run: async (operation, input, signal) => {
        await nimi.prepareSession(signal);
        signal.throwIfAborted();
        return jobs!.run(operation, input, signal);
      } });
      supervisor = backend;
      console.log(`[supervisor] backend root: ${backendRoot()} (packaged=${app.isPackaged})`);
      backend.subscribe((s) => {
        const detail = s.message ? ` — ${s.message}` : '';
        console.log(`[supervisor] stage=${s.stage} managed=${s.managed} port=${s.port}${detail}`);
      });
      installAppProtocol(
        () => backend.baseUrl,
        RENDERER_DIR,
        () => backend.requestHeaders(),
      );
      registerIpc(
        backend,
        () => mainWindow,
        async () => {
          quitting = true;
          await tearDownNativeResources();
          app.exit(0);
        },
      );
      if (is.dev) {
        const proxy = await startDevBackendProxy(
          () => backend.baseUrl,
          () => backend.requestHeaders(),
          Number(process.env.VOICESTUDIO_ELECTRON_PROXY_PORT) || 3903,
        );
        closeDevProxy = proxy.close;
      }
      mainWindow = createWindow();
      closeWatch = installWatchFolders(
        () => mainWindow,
        () => backend.baseUrl,
        () => backend.requestHeaders(),
      );
      if (process.platform === 'darwin' && !app.isPackaged) app.dock?.setIcon(nativeAppIconPath);
      tray = new Tray(brandIconPath('32x32.png'));
      tray.setToolTip('VoiceStudio');
      closeCapture = installNativeCapture(PRELOAD_PATH, tray, () => mainWindow);
      tray.on('click', () => {
        if (!isLiveWindow(mainWindow)) mainWindow = createWindow();
        activateLiveWindow(mainWindow);
      });
      observeMainProcessTask(backend.start(), mainErrors, 'Backend startup');

      app.on('activate', () => {
        if (!isLiveWindow(mainWindow)) mainWindow = createWindow();
      });
    })
    .catch((error) => {
      // Keep initialization failures out of Node's unhandled-rejection path,
      // while retaining durable evidence for the next launch and repair agent.
      mainErrors.record(error, 'unhandledRejection');
      console.error('[startup] Electron initialization failed', error);
      app.exit(1);
    });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });

  // The first pass gives renderer persistence a bounded chance to settle, then
  // tears native resources down. The second pass exits without re-entering.
  app.on('before-quit', (event) => {
    if (quitting) return;
    event.preventDefault();
    observeMainProcessTask(beginOrderlyQuit(), mainErrors, 'Orderly quit');
  });
}
